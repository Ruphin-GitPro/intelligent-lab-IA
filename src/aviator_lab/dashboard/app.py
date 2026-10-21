"""Tableau de bord local en français pour le laboratoire Aviator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import streamlit as st

from aviator_lab import __version__, config
from aviator_lab.analytics import (
    descriptive_stats,
    financial_summary,
    next_round_interval,
    next_round_median,
    rolling_median_backtest,
)
from aviator_lab.collection.history import SOURCE as FIREFOX_HISTORY_SOURCE
from aviator_lab.analytics.withdrawal import (
    DEFAULT_TARGETS,
    MIN_EVALUATION_ROUNDS,
    NO_ADVANTAGE,
    analyze_target,
    compare_targets_out_of_sample,
    minimum_rounds_for_evaluation,
    simulate_fixed_target,
)
from aviator_lab.ingestion import import_bets, import_rounds
from aviator_lab.probability import empirical_exceedance_probability
from aviator_lab.rng_research import (
    autocorrelation_diagnostic,
    compare_periods,
    correct_p_values,
    runs_test,
)
from aviator_lab.simulation import bootstrap_simulation
from aviator_lab.storage import RoundRepository


ORIGIN_LABELS = {
    "real": "Réelles",
    "synthetic": "Synthétiques",
    "simulated": "Simulées",
}
ORIGINS = tuple(ORIGIN_LABELS)
PAGES = (
    "Vue générale",
    "Historique des manches",
    "Distribution des multiplicateurs",
    "Probabilités empiriques",
    "Estimation du prochain tour",
    "Analyses statistiques",
    "Mes résultats financiers",
    "Simulations fictives",
    "Assistant de mise et de retrait",
    "Qualité des données",
    "Collecte Firefox",
    "Paramètres et exportations",
)
HEARTBEAT_FRESHNESS_SECONDS = 90
COLLECTION_FRESHNESS_WINDOW = timedelta(hours=24)
WITHDRAWAL_REPORT_THRESHOLDS = (1.5, 2.0, 3.0, 5.0)


@st.cache_resource
def get_repository() -> RoundRepository:
    """Crée le schéma au premier lancement et réutilise le moteur local."""
    repository = RoundRepository(config.DB_PATH)
    repository.initialize()
    return repository


def _round_frame(rounds) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": row.id,
                "ID externe": row.external_id,
                "Multiplicateur": float(row.multiplier),
                "Date et heure": row.played_at,
                "Enregistré localement": row.recorded_at,
                "Source": row.source,
                "Origine": row.origin,
                "Validation": row.validation_status,
                "Motif d'invalidation": row.invalid_reason,
            }
            for row in rounds
        ]
    )


def _temporal_frequency_frame(rounds, threshold: float) -> pd.DataFrame:
    """Agrège par date saisie, sans inventer de date pour les manches non datées."""
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or threshold < 1
    ):
        raise ValueError("Le seuil doit être un nombre supérieur ou égal à 1.")
    dated = [
        {"Période": row.played_at.date(), "Multiplicateur": float(row.multiplier)}
        for row in rounds
        if row.played_at is not None
    ]
    columns = ["Période", "Manches datées", "Fréquence observée ≥ seuil"]
    if not dated:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(dated)
    frame["Seuil atteint"] = frame["Multiplicateur"] >= float(threshold)
    return (
        frame.groupby("Période", as_index=False, sort=True)
        .agg(
            **{
                "Manches datées": ("Multiplicateur", "size"),
                "Fréquence observée ≥ seuil": ("Seuil atteint", "mean"),
            }
        )
    )


def _bet_frame(bets) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": row.id,
                "ID externe": row.external_id,
                "ID manche": row.round_id,
                "Date et heure": row.bet_at,
                "Mise": row.stake_amount,
                "Retrait (x)": row.cashout_multiplier,
                "Montant retourné": row.returned_amount,
                "Résultat net": row.net_amount,
                "Devise": row.currency,
                "Source": row.source,
                "Origine": row.origin,
            }
            for row in bets
        ]
    )


def _import_upload(upload, importer, repository, *, default_origin: str):
    suffix = Path(upload.name).suffix.lower()
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as temp_file:
            temp_file.write(upload.getvalue())
            temp_file.flush()
            return importer(temp_file.name, repository, default_origin=default_origin)
    except (ValueError, OSError) as exc:
        st.error(f"Import impossible : {exc}")
        return None


def _format_number(value, digits: int = 3) -> str:
    return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ")


def _show_import_report(report) -> None:
    st.success(
        f"Import terminé : {report.imported} ajoutée(s), "
        f"{len(report.rejected)} rejetée(s), {report.skipped_empty} ligne(s) vide(s)."
    )
    if report.rejected:
        st.dataframe(
            pd.DataFrame(
                [{"Ligne": issue.row_number, "Motif": issue.message} for issue in report.rejected]
            ),
            hide_index=True,
            use_container_width=True,
        )


def _decision_status(values: list[float], *, origin: str) -> tuple[str, str]:
    """Applique une règle d'abstention; aucune donnée n'est certifiée par Bet261."""
    if len(values) < 100:
        return (
            "DONNÉES INSUFFISANTES",
            f"Moins de 100 manches valides (n={len(values)}); ce seuil est un repère minimal interne.",
        )
    p_values = []
    if len(set(values)) > 1:
        try:
            autocorrelation = autocorrelation_diagnostic(
                values, origin=origin, lags=min(10, len(values) - 1)
            )
            p_values.extend(
                item.p_value for item in autocorrelation.lags if math.isfinite(item.p_value)
            )
        except ValueError:
            pass
    try:
        runs_p_value = runs_test(values, origin=origin).p_value
        if math.isfinite(runs_p_value):
            p_values.append(runs_p_value)
    except ValueError:
        pass
    if p_values:
        adjusted = correct_p_values(p_values, method="holm", alpha=0.01)
        if any(result.rejected for result in adjusted.results):
            return (
                "AUCUN SIGNAL FIABLE — NE PAS MISER",
                "Au moins un diagnostic exploratoire reste significatif après correction de Holm (α=1 %). "
                "Cet écart est à examiner, pas à exploiter. Les données ne sont pas vérifiées par Bet261; "
                "cela ne démontre ni prédictibilité ni rentabilité.",
            )
    return (
        "AUCUN SIGNAL FIABLE — NE PAS MISER",
        "Aucun avantage n'est établi par ces diagnostics et la provenance Bet261 n'est pas vérifiée. "
        "L'absence d'écart détecté ne prédit pas la prochaine manche.",
    )


def _evaluation_counts(n: int, study_fraction: float = 0.7) -> tuple[int, int, int, int]:
    """Return study, later evaluation, missing evaluation and total missing counts."""
    study_n = int(n * study_fraction)
    evaluation_n = n - study_n
    minimum_total = minimum_rounds_for_evaluation(
        study_fraction=study_fraction,
        min_evaluation_rounds=MIN_EVALUATION_ROUNDS,
    )
    return study_n, evaluation_n, max(0, MIN_EVALUATION_ROUNDS - evaluation_n), max(0, minimum_total - n)


def _validation_category(n: int, comparison, *, fresh: bool, reliable: bool) -> str:
    """Describe evidence status without turning it into a next-round signal."""
    minimum_total = minimum_rounds_for_evaluation()
    if n < minimum_total or not fresh or not reliable or comparison is None:
        return "DONNÉES INSUFFISANTES"
    if comparison.verdict != NO_ADVANTAGE:
        return "RÉSULTAT VALIDÉ SUR L'ÉCHANTILLON DE TEST"
    chosen = next(
        (row for row in comparison.rows if row.target == comparison.selected_target), None
    )
    if chosen is not None and chosen.evaluation_expectation_low > 0:
        return "RÉSULTAT PROMETTEUR MAIS NON VALIDÉ"
    return "AUCUN AVANTAGE STATISTIQUE DÉMONTRÉ"


def _page_overview(repository) -> None:
    st.header("Vue générale")
    st.caption("Les fréquences historiques décrivent les données observées, pas le prochain résultat.")
    columns = st.columns(3)
    for column, origin in zip(columns, ORIGINS):
        rounds = repository.list_rounds(origin=origin)
        column.metric(f"Manches {ORIGIN_LABELS[origin].lower()}", len(rounds))
    st.subheader("Résumé par origine")
    overview = []
    for origin in ORIGINS:
        rounds = repository.list_rounds(origin=origin)
        summary = descriptive_stats(
            [round_.multiplier for round_ in rounds], origin=origin
        )
        overview.append(
            {
                "Origine": ORIGIN_LABELS[origin],
                "Manches valides": summary.n_valid,
                "Moyenne": summary.mean,
                "Médiane": summary.median,
                "Dates absentes": sum(round_.played_at is None for round_ in rounds),
                "État d'analyse": _decision_status(
                    [float(round_.multiplier) for round_ in rounds], origin=origin
                )[0],
            }
        )
    st.dataframe(pd.DataFrame(overview), hide_index=True, use_container_width=True)
    st.info("Les données réelles, synthétiques et simulées sont affichées séparément.")
    st.caption(
        "Critères : moins de 100 manches valides = données insuffisantes; au-delà, "
        "l'assistant reste sur AUCUN SIGNAL FIABLE — NE PAS MISER. Un écart exploratoire est signalé pour examen, "
        "mais ne prouve ni prédictibilité ni avantage. La source Bet261 n'est pas vérifiée."
    )


def _local_collection_status() -> dict | None:
    """Lit l'état de l'API liée à localhost sans bloquer le tableau de bord."""
    try:
        with urlopen("http://127.0.0.1:8765/api/v1/status", timeout=0.8) as response:
            try:
                payload = json.loads(response.read(16_384))
            except (TypeError, ValueError):
                return {
                    "_api_reachable": True,
                    "_status_valid": False,
                    "_status_error": "La réponse de l'API n'est pas un JSON exploitable.",
                }
    except HTTPError as exc:
        return {
            "_api_reachable": True,
            "_status_valid": False,
            "_status_error": f"Réponse HTTP {exc.code}",
        }
    except (OSError, URLError, TimeoutError):
        return None
    if not isinstance(payload, dict):
        return {
            "_api_reachable": True,
            "_status_valid": False,
            "_status_error": "La réponse de l'API n'est pas un objet JSON.",
        }
    return payload


def _utc_datetime(value) -> datetime | None:
    """Normalise une date enregistrée en UTC; SQLite peut omettre son fuseau."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    try:
        if value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _collection_state(status: dict | None, rounds, *, now: datetime | None = None) -> dict:
    """Déduit l'état observable sans confondre heartbeat et manche persistée."""
    now_utc = _utc_datetime(now or datetime.now(timezone.utc))
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    valid_rounds = [
        row for row in rounds
        if getattr(row, "validation_status", None) == "valid"
        and math.isfinite(float(row.multiplier))
        and float(row.multiplier) >= 1
    ]
    collector_rounds = [
        row for row in valid_rounds
        if getattr(row, "source", None) == FIREFOX_HISTORY_SOURCE
    ]
    recorded = [(row, _utc_datetime(getattr(row, "recorded_at", None))) for row in valid_rounds]
    latest = max(
        ((row, at) for row, at in recorded if at is not None),
        key=lambda item: item[1],
        default=None,
    )
    freshness_start = now_utc - COLLECTION_FRESHNESS_WINDOW
    collector_recorded = [
        (row, _utc_datetime(getattr(row, "recorded_at", None))) for row in collector_rounds
    ]
    recent_rounds = [
        row for row, at in collector_recorded
        if at is not None and freshness_start <= at <= now_utc
    ]
    unknown_timestamps = any(at is None for _, at in collector_recorded)

    result = {
        "state": "ÉTAT INCONNU",
        "cause": "Les informations disponibles ne permettent pas de confirmer l'état de la collecte.",
        "api_reachable": None,
        "heartbeat_recent": None,
        "heartbeat_age": None,
        "recent_round_count": len(recent_rounds),
        "valid_round_count": len(valid_rounds),
        "latest_round": latest[0] if latest else None,
        "latest_recorded_at": latest[1] if latest else None,
        "last_error": None,
        "freshness_window": COLLECTION_FRESHNESS_WINDOW,
    }
    if status is None:
        result.update(
            state="COLLECTE EN ERREUR",
            cause="L'API locale n'a pas répondu; sa disponibilité et son heartbeat ne peuvent pas être lus.",
            api_reachable=False,
            heartbeat_recent=False,
        )
        return result
    if status.get("_status_valid") is False:
        result.update(
            api_reachable=True,
            cause=status.get("_status_error", result["cause"]),
        )
        return result
    if status.get("ok") is not True:
        result.update(api_reachable=True)
        return result

    result["api_reachable"] = True
    heartbeat_age = status.get("last_heartbeat_age_seconds")
    connected = status.get("collector_connected")
    if isinstance(heartbeat_age, (int, float)) and not isinstance(heartbeat_age, bool):
        result["heartbeat_age"] = float(heartbeat_age)
    if connected is True and result["heartbeat_age"] is not None:
        if 0 <= result["heartbeat_age"] <= HEARTBEAT_FRESHNESS_SECONDS:
            result["heartbeat_recent"] = True
        elif result["heartbeat_age"] > HEARTBEAT_FRESHNESS_SECONDS:
            result.update(
                state="COLLECTE EN ERREUR",
                cause=(f"Le dernier heartbeat date de {result['heartbeat_age']:.1f} s; "
                       f"le seuil est {HEARTBEAT_FRESHNESS_SECONDS} s."),
                heartbeat_recent=False,
            )
            return result
        else:
            result["cause"] = "L'âge du heartbeat fourni par l'API est incohérent."
            return result
    elif connected is False and result["heartbeat_age"] is None:
        result.update(
            state="COLLECTE EN ERREUR",
            cause="Aucun heartbeat de l'extension n'a été reçu depuis le démarrage de l'API.",
            heartbeat_recent=False,
        )
        return result
    elif connected is False and result["heartbeat_age"] is not None:
        if result["heartbeat_age"] >= HEARTBEAT_FRESHNESS_SECONDS:
            result.update(
                state="COLLECTE EN ERREUR",
                cause=(f"Le dernier heartbeat date de {result['heartbeat_age']:.1f} s; "
                       f"le seuil est {HEARTBEAT_FRESHNESS_SECONDS} s."),
                heartbeat_recent=False,
            )
            return result
        result["cause"] = "L'indicateur de heartbeat de l'API est incohérent avec son âge déclaré."
        return result
    else:
        result["cause"] = "L'API ne fournit pas assez d'informations sur le heartbeat."
        return result

    ingest = status.get("history_ingest")
    ingest = ingest if isinstance(ingest, dict) else {}
    error = status.get("last_collection_error")
    error_at = _utc_datetime(status.get("last_collection_error_at"))
    saved_at = _utc_datetime(ingest.get("last_saved_at"))
    result["last_error"] = error
    current_error = bool(error) and (error_at is None or saved_at is None or error_at > saved_at)
    if current_error:
        result.update(
            state="COLLECTE EN ERREUR",
            cause=f"Dernière erreur de collecte connue : {error}",
        )
        return result

    if recent_rounds:
        result.update(
            state="COLLECTE ACTIVE",
            cause=(f"{len(recent_rounds)} manche(s) valide(s) ont été enregistrées dans SQLite "
                   f"au cours des {COLLECTION_FRESHNESS_WINDOW.total_seconds() / 3600:g} dernières heures."),
        )
        return result
    if unknown_timestamps:
        result["cause"] = "Au moins une manche valide n'a pas d'horodatage d'enregistrement exploitable."
        return result
    result.update(
        state="COLLECTE EN ATTENTE",
        cause=("L'API est joignable et le heartbeat est récent, mais aucune nouvelle manche valide "
               f"n'a été enregistrée dans SQLite au cours des {COLLECTION_FRESHNESS_WINDOW.total_seconds() / 3600:g} dernières heures. "
               "L'extension peut communiquer sans qu'une nouvelle manche ait encore été enregistrée."),
    )
    return result


def _page_collection(repository) -> None:
    st.header("Collecte Firefox Bet261")
    st.warning(
        "Collecte automatique active mais non validée par l'éditeur : les valeurs sont lues dans la liste "
        "d'historique affichée par la page. Aucun identifiant officiel de manche n'existe."
    )
    status = _local_collection_status()
    if status is None:
        st.error("API locale hors ligne. Démarrez-la avec `python -m aviator_lab.collection.api`.")
    else:
        if status.get("collector_connected"):
            st.success("Extension Firefox reliée à l'API locale (heartbeat reçu récemment).")
        else:
            st.warning("Aucun heartbeat récent de l'extension Firefox.")
        st.metric("Lignes d'origine « real » en SQLite", status.get("round_count", 0))
        heartbeat_age = status.get("last_heartbeat_age_seconds")
        st.metric(
            "Âge du dernier heartbeat",
            f"{heartbeat_age:g} s" if isinstance(heartbeat_age, (int, float)) else "aucun",
        )
        ingest = status.get("history_ingest") or {}
        st.caption(
            f"Enregistrées par la collecte : {ingest.get('saved_rounds', 0)} · doublons ignorés : "
            f"{ingest.get('duplicate_rounds', 0)} · transitions refusées : {ingest.get('rejected_transitions', 0)} "
            "(depuis le démarrage de l'API)."
        )
    rounds = repository.list_rounds(origin="real")
    if rounds:
        latest = rounds[-1]
        columns = st.columns(3)
        columns[0].metric("Dernier multiplicateur enregistré", f"{latest.multiplier}×")
        columns[1].metric(
            "Horodatage fourni", latest.played_at.isoformat() if latest.played_at else "absent"
        )
        columns[2].metric("Provenance déclarée", latest.source)
        st.caption(
            "L'horodatage est l'heure de détection par l'extension (environ 1 s), pas l'heure exacte de fin de manche."
        )
        st.dataframe(_round_frame(rounds[-20:][::-1]), hide_index=True, use_container_width=True)
    else:
        st.info("Aucun résultat réel enregistré dans SQLite.")


@st.fragment(run_every=5)
def _render_live_content(repository, page: str, origin: str) -> None:
    """Rafraîchit l'état et la vue courante sans recharger toute l'application."""
    status = _local_collection_status()
    if page != "Assistant de mise et de retrait":
        st.warning("Collecte Bet261 non vérifiée par l'éditeur — lecture de l'affichage seulement, aucune aide au gain.")
        if status is None:
            st.error("Collecte locale : API hors ligne")
        elif status.get("collector_connected"):
            st.success(
                f"Extension reliée · collecte automatique active · {status.get('round_count', 0)} ligne(s) d'origine real en SQLite"
            )
        else:
            st.info(
                "Selon la colonne « Source » : `firefox_history_window_v1` = lue automatiquement dans l'affichage de la page; "
                "« saisie manuelle » ou un fichier importé = saisie par vous. "
                "L'origine « réelle » ne signifie pas que Bet261 a vérifié ces données."
            )
    if page != "Assistant de mise et de retrait" and status and status.get("last_collection_error"):
        st.error(
            "Dernière erreur API : "
            f"{status['last_collection_error']} · {status.get('last_collection_error_at', 'heure inconnue')} "
            f"(total : {status.get('collection_error_count', 0)})"
        )

    if page == "Vue générale":
        _page_overview(repository)
    elif page == "Historique des manches":
        _page_history(repository, origin)
    elif page == "Distribution des multiplicateurs":
        _page_distribution(repository, origin)
    elif page == "Probabilités empiriques":
        _page_probabilities(repository, origin)
    elif page == "Estimation du prochain tour":
        _page_next_round_estimate(repository, origin)
    elif page == "Analyses statistiques":
        _page_advanced(repository, origin)
    elif page == "Mes résultats financiers":
        _page_finances(repository, origin)
    elif page == "Simulations fictives":
        _page_simulations(repository, origin)
    elif page == "Assistant de mise et de retrait":
        _page_withdrawal(repository, origin, collection_status=status)
    elif page == "Qualité des données":
        _page_quality(repository)
    elif page == "Collecte Firefox":
        _page_collection(repository)
    else:
        _page_settings(repository)


def _page_history(repository, origin: str) -> None:
    st.header("Historique des manches")
    st.caption(f"Origine sélectionnée : **{ORIGIN_LABELS[origin]}**")
    st.info(
        "La source indique une saisie manuelle ou un fichier importé. "
        "L'origine « réelle » ne signifie pas que Bet261 a vérifié ces données."
    )
    rounds = repository.list_rounds(origin=origin)
    with st.expander("Saisir une manche manuellement"):
        with st.form("add_round_form"):
            multiplier = st.text_input("Multiplicateur final", placeholder="2.35")
            external_id = st.text_input("Identifiant externe (facultatif)")
            source = st.text_input("Source", value="saisie manuelle")
            played_at_text = st.text_input(
                "Date et heure ISO 8601 (facultatif)", placeholder="2026-10-18T14:30:00"
            )
            submitted = st.form_submit_button("Valider et enregistrer")
        if submitted:
            try:
                played_at = datetime.fromisoformat(played_at_text) if played_at_text.strip() else None
                repository.add_round(
                    multiplier,
                    source=source,
                    origin=origin,
                    external_id=external_id or None,
                    played_at=played_at,
                )
                st.success("Manche enregistrée.")
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
    with st.expander("Importer des manches CSV ou Excel"):
        uploaded = st.file_uploader(
            "Fichier de manches", type=["csv", "xlsx", "xlsm"], key="round_upload"
        )
        if uploaded is not None and st.button("Importer les manches", key="round_import_button"):
            report = _import_upload(uploaded, import_rounds, repository, default_origin=origin)
            if report is not None:
                _show_import_report(report)
                st.rerun()
    if rounds:
        st.dataframe(_round_frame(rounds), hide_index=True, use_container_width=True)
    else:
        st.info("Aucune manche de cette origine. Vous pouvez en saisir ou en importer.")


def _page_distribution(repository, origin: str) -> None:
    st.header("Distribution des multiplicateurs")
    rounds = repository.list_rounds(origin=origin)
    if not rounds:
        st.info("Aucune observation disponible pour cette origine.")
        return
    values = [float(row.multiplier) for row in rounds]
    summary = descriptive_stats(values, origin=origin)
    bins = pd.DataFrame(
        [
            {
                "Intervalle": f"[{item.lower:g}, {item.upper:g})" if item.upper else f"[{item.lower:g}, +∞)",
                "Nombre": item.count,
            }
            for item in summary.histogram
        ]
    )
    st.plotly_chart(
        px.bar(bins, x="Intervalle", y="Nombre", title="Effectifs par intervalle prédéfini"),
        use_container_width=True,
    )
    st.caption(f"Origine : {ORIGIN_LABELS[origin]} · Échantillon valide : n = {summary.n_valid}")
    st.subheader("Évolution temporelle des fréquences observées")
    time_threshold = st.number_input(
        "Seuil temporel (M ≥ x)", min_value=1.0, value=2.0, step=0.1,
        key="temporal_frequency_threshold",
    )
    temporal = _temporal_frequency_frame(rounds, time_threshold)
    if temporal.empty:
        st.info("Aucune manche datée : l'évolution temporelle n'est pas calculable.")
    else:
        st.plotly_chart(
            px.line(
                temporal,
                x="Période",
                y="Fréquence observée ≥ seuil",
                markers=True,
                hover_data=["Manches datées"],
                title=f"Fréquence quotidienne observée avec M ≥ {time_threshold:g}x",
            ),
            use_container_width=True,
        )
        st.dataframe(temporal, hide_index=True, use_container_width=True)
    st.caption(
        "Groupement par date telle qu'elle est fournie; les manches sans horodatage sont exclues. "
        "Une fréquence quotidienne décrit l'échantillon disponible, pas une probabilité théorique."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {"Indicateur": "Minimum", "Valeur": summary.minimum},
                {"Indicateur": "Maximum", "Valeur": summary.maximum},
                {"Indicateur": "Moyenne", "Valeur": summary.mean},
                {"Indicateur": "Médiane", "Valeur": summary.median},
                {"Indicateur": "Écart-type (échantillon)", "Valeur": summary.sample_std_dev},
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )


def _page_probabilities(repository, origin: str) -> None:
    st.header("Probabilités empiriques")
    rounds = repository.list_rounds(origin=origin)
    if not rounds:
        st.info("Aucune observation disponible pour cette origine.")
        return
    values = [float(row.multiplier) for row in rounds]
    thresholds = st.multiselect(
        "Seuils à étudier (M ≥ x)",
        options=list(config.DEFAULT_THRESHOLDS),
        default=list(config.DEFAULT_THRESHOLDS),
    )
    confidence = st.select_slider(
        "Niveau de confiance", options=[0.90, 0.95, 0.99], value=0.95, format_func=lambda x: f"{x:.0%}"
    )
    estimates = []
    for threshold in thresholds:
        estimate = empirical_exceedance_probability(
            values, threshold, origin=origin, confidence_level=confidence
        )
        estimates.append(
            {
                "Seuil x": threshold,
                "Réussites": estimate.successes,
                "Échantillon n": estimate.n_valid,
                "Fréquence empirique": estimate.estimate,
                f"Borne basse ({confidence:.0%})": estimate.confidence_low,
                f"Borne haute ({confidence:.0%})": estimate.confidence_high,
            }
        )
    st.dataframe(pd.DataFrame(estimates), hide_index=True, use_container_width=True)
    st.warning(
        "L'intervalle de Wilson suppose des manches indépendantes et une probabilité stable. "
        "Une fréquence observée n'est pas la probabilité certaine du prochain résultat."
    )


def _page_next_round_estimate(repository, origin: str) -> None:
    st.header("Estimation probabiliste du prochain tour")
    rounds = repository.list_rounds(origin=origin)
    values = [float(row.multiplier) for row in rounds if row.validation_status == "valid"]
    if not values:
        st.info("Aucun multiplicateur valide disponible pour établir une estimation historique.")
        return

    series = pd.Series(values, dtype="float64")
    confidence_band = st.select_slider(
        "Fourchette centrale affichée", options=[0.50, 0.80, 0.90], value=0.80,
        format_func=lambda value: f"{value:.0%}", key="next_round_band",
    )
    tail = (1 - confidence_band) / 2
    lower = float(series.quantile(tail))
    middle = float(series.quantile(0.5))
    upper = float(series.quantile(1 - tail))
    columns = st.columns(3)
    columns[0].metric("Valeur centrale historique", f"{middle:.2f}×")
    columns[1].metric(f"Borne basse · {confidence_band:.0%}", f"{lower:.2f}×")
    columns[2].metric(f"Borne haute · {confidence_band:.0%}", f"{upper:.2f}×")
    st.caption(
        f"Estimation calculée à partir des {len(values)} manches valides sélectionnées ({origin}); "
        "les bornes sont les quantiles observés de l'historique."
    )

    thresholds = st.multiselect(
        "Probabilité historique que le prochain résultat atteigne le seuil",
        options=list(config.DEFAULT_THRESHOLDS), default=[1.5, 2.0, 3.0, 5.0],
        key="next_round_thresholds",
    )
    confidence = 0.95
    estimates = []
    for threshold in thresholds:
        estimate = empirical_exceedance_probability(
            values, threshold, origin=origin, confidence_level=confidence
        )
        estimates.append({
            "Seuil": f"≥ {threshold:g}×", "Fréquence observée": f"{estimate.estimate:.1%}",
            "IC Wilson 95%": f"{estimate.confidence_low:.1%} – {estimate.confidence_high:.1%}",
            "Réussites / manches": f"{estimate.successes} / {estimate.n_valid}",
        })
    if estimates:
        st.dataframe(pd.DataFrame(estimates), hide_index=True, use_container_width=True)

    st.warning(
        "Cette estimation résume la distribution passée et suppose que les manches futures lui ressemblent. "
        "Elle ne connaît pas le résultat exact du prochain tour; l'intervalle affiché n'est pas une garantie. "
        "Une suite de résultats récents ne permet pas de déduire le prochain multiplicateur."
    )


def _page_advanced(repository, origin: str) -> None:
    st.header("Analyses statistiques")
    rounds = repository.list_rounds(origin=origin)
    if not rounds:
        st.info("Aucune observation disponible pour cette origine.")
        return
    values = [float(row.multiplier) for row in rounds]
    summary = descriptive_stats(values, origin=origin)
    columns = st.columns(4)
    columns[0].metric("Échantillon valide", summary.n_valid)
    columns[1].metric("Données manquantes", summary.n_missing)
    columns[2].metric("Plus longue séquence ≥ 2x", summary.longest_above_streak)
    columns[3].metric("Plus longue séquence < 2x", summary.longest_below_streak)

    st.subheader("Diagnostics exploratoires")
    st.caption("Les tests demandent des séries assez longues et complètes; les écarts ne prouvent pas un avantage.")
    lags = st.number_input("Retards Ljung–Box", min_value=1, max_value=50, value=min(10, max(1, len(values) - 1)))
    if st.button("Tester l'autocorrélation"):
        try:
            result = autocorrelation_diagnostic(values, origin=origin, lags=int(lags))
            st.write(f"H₀ : {result.null_hypothesis}")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Retard": item.lag,
                            "Statistique Q": item.statistic,
                            "p-value brute": item.p_value,
                            "p-value Holm": item.p_value_holm,
                        }
                        for item in result.lags
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            st.info(result.limitation)
        except ValueError as exc:
            st.error(str(exc))

    if st.button("Tester les séquences autour de la médiane"):
        try:
            result = runs_test(values, origin=origin)
            st.write(
                f"n={result.n}, séquences={result.runs}, seuil={result.cutoff:.4g}, "
                f"z={result.z_statistic:.3f}, p={result.p_value:.4g}."
            )
            st.info(result.limitation)
        except ValueError as exc:
            st.error(str(exc))

    if st.button("Comparer la première et la seconde moitié"):
        split = len(values) // 2
        try:
            result = compare_periods(values[:split], values[split:], origin=origin)
            st.write(
                f"Première moitié n={result.n_first}, médiane={result.median_first:.4g}; "
                f"seconde moitié n={result.n_second}, médiane={result.median_second:.4g}; "
                f"U={result.u_statistic:.3f}, p={result.p_value:.4g}."
            )
            st.info(result.limitation)
        except ValueError as exc:
            st.error(str(exc))

    with st.expander("Correction de plusieurs p-values"):
        p_values_text = st.text_input("p-values séparées par des virgules", placeholder="0.02, 0.1, 0.4")
        method = st.selectbox("Méthode", ["holm", "fdr_bh"])
        if st.button("Corriger les p-values"):
            try:
                p_values = [float(item.strip()) for item in p_values_text.split(",") if item.strip()]
                correction = correct_p_values(p_values, method=method)
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"p-value brute": item.raw, "p-value ajustée": item.adjusted, "Rejet H₀": item.rejected}
                            for item in correction.results
                        ]
                    ),
                    hide_index=True,
                )
            except ValueError as exc:
                st.error(str(exc))


def _page_finances(repository, origin: str) -> None:
    st.header("Mes résultats financiers")
    with st.expander("Saisir un pari personnel"):
        with st.form("add_bet_form"):
            source = st.text_input("Source", value="saisie manuelle", key="bet_source")
            external_id = st.text_input("Identifiant externe (facultatif)", key="bet_external_id")
            round_id_text = st.text_input("ID interne de manche (facultatif)", key="bet_round_id")
            stake = st.text_input("Mise (facultatif)", key="bet_stake")
            cashout = st.text_input("Multiplicateur de retrait (facultatif)", key="bet_cashout")
            returned = st.text_input("Montant effectivement retourné (facultatif)", key="bet_returned")
            net = st.text_input("Résultat net fourni (facultatif)", key="bet_net")
            currency = st.text_input("Devise, code de 3 lettres (facultatif)", key="bet_currency")
            bet_at_text = st.text_input(
                "Date et heure ISO 8601 (facultatif)", placeholder="2026-10-18T14:30:00", key="bet_at"
            )
            submitted = st.form_submit_button("Enregistrer le pari")
        if submitted:
            try:
                round_id = int(round_id_text) if round_id_text.strip() else None
                bet = repository.add_bet(
                    source=source,
                    origin=origin,
                    external_id=external_id or None,
                    round_id=round_id,
                    stake_amount=stake or None,
                    cashout_multiplier=cashout or None,
                    returned_amount=returned or None,
                    net_amount=net or None,
                    currency=currency or None,
                    bet_at=datetime.fromisoformat(bet_at_text) if bet_at_text.strip() else None,
                )
                st.success(f"Pari #{bet.id} enregistré. Aucun pari réel n'a été effectué.")
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
    with st.expander("Importer des paris CSV ou Excel"):
        upload = st.file_uploader("Fichier de paris", type=["csv", "xlsx", "xlsm"], key="bet_upload")
        if upload is not None and st.button("Importer les paris"):
            report = _import_upload(upload, import_bets, repository, default_origin=origin)
            if report is not None:
                _show_import_report(report)
                st.rerun()

    bets = repository.list_bets(origin=origin)
    currencies = {bet.currency for bet in bets}
    if len(currencies) > 1:
        currency_choices = sorted(currencies, key=lambda value: value or "")
        selected_currency = st.selectbox(
            "Filtrer par devise",
            currency_choices,
            format_func=lambda value: value or "Devise non précisée",
        )
        bets = [bet for bet in bets if bet.currency == selected_currency]
    summary = financial_summary(bets, origin=origin)
    st.caption(f"Origine : {ORIGIN_LABELS[origin]} · Devise : {summary.currency or 'non précisée'}")
    cols = st.columns(4)
    cols[0].metric("Paris", summary.bet_count)
    cols[1].metric("Mises connues", _format_number(summary.total_staked), f"n={summary.bets_with_stake}")
    cols[2].metric("Retours connus", _format_number(summary.total_returned), f"n={summary.bets_with_return}")
    cols[3].metric("Résultat net connu", _format_number(summary.net_result), f"n={summary.bets_with_net}")
    cols = st.columns(4)
    cols[0].metric("Gagnants", summary.wins)
    cols[1].metric("Perdants", summary.losses)
    cols[2].metric("Égalité", summary.break_even)
    cols[3].metric("Série max. de pertes", summary.longest_loss_streak)
    st.caption(
        f"Paris complets : {summary.complete_bet_count} · incomplets : {summary.incomplete_bet_count} · "
        f"Volatilité nette (écart-type) : {_format_number(summary.net_sample_std_dev)}"
    )
    if summary.drawdown_complete:
        st.metric("Drawdown max. du résultat cumulé", _format_number(summary.max_drawdown))
        if summary.cumulative_net_curve:
            starting_capital = st.number_input(
                "Capital initial pour la courbe (saisi volontairement)",
                min_value=0.0,
                value=0.0,
                step=10.0,
                help="La courbe ajoute les résultats nets connus au capital initial indiqué; dépôts et retraits externes ne sont pas suivis.",
            )
            st.metric(
                "Capital final dérivé",
                _format_number(starting_capital + float(summary.net_result)),
            )
            frame = pd.DataFrame(
                [{"Pari (ordre d'insertion)": bet_id,
                  "Capital dérivé": starting_capital + float(value)}
                 for bet_id, value in summary.cumulative_net_curve]
            )
            st.plotly_chart(
                px.line(frame, x="Pari (ordre d'insertion)", y="Capital dérivé"),
                use_container_width=True,
            )
    else:
        st.info("Drawdown et courbe indisponibles : au moins un résultat net manque.")
    if bets:
        st.dataframe(_bet_frame(bets), hide_index=True, use_container_width=True)
    else:
        st.info("Aucun pari personnel enregistré pour cette origine.")


def _page_simulations(repository, origin: str) -> None:
    st.header("Simulations à argent fictif")
    rounds = repository.list_rounds(origin=origin)
    st.caption(f"Les multiplicateurs de référence proviennent des données {ORIGIN_LABELS[origin].lower()}.")
    columns = st.columns(3)
    stake = columns[0].number_input("Mise fictive", min_value=0.01, value=1.0, step=0.5)
    cashout = columns[1].number_input("Retrait hypothétique (x)", min_value=1.0, value=2.0, step=0.5)
    bankroll = columns[2].number_input("Bankroll fictive", min_value=0.01, value=100.0, step=10.0)
    columns = st.columns(3)
    max_rounds = columns[0].number_input("Manches max. par essai", min_value=1, value=100, step=10)
    trials = columns[1].number_input("Nombre d'essais", min_value=1, value=1000, step=100)
    seed = columns[2].number_input("Graine aléatoire", min_value=0, value=42, step=1)
    use_loss_limit = st.checkbox("Activer une limite de pertes")
    loss_limit = st.number_input("Limite de perte fictive", min_value=0.01, value=25.0) if use_loss_limit else None
    st.warning(
        "Hypothèse : retour brut = mise × seuil si le multiplicateur atteint le retrait, sinon 0. "
        "Bootstrap IID, sans frais; ceci ne confirme pas le barème réel du jeu. "
        "L'historique sert à la fois de référence et de source de rééchantillonnage : "
        "ce n'est pas une validation hors échantillon."
    )
    if st.button("Lancer la simulation"):
        try:
            values = [round_.multiplier for round_ in rounds]
            result = bootstrap_simulation(
                values,
                reference_origin=origin,
                stake=stake,
                cashout_multiplier=cashout,
                starting_bankroll=bankroll,
                max_rounds=int(max_rounds),
                trials=int(trials),
                loss_limit=loss_limit,
                seed=int(seed),
            )
            dates = [round_.played_at for round_ in rounds if round_.played_at]
            repository.record_analysis(
                analysis_type="bootstrap_simulation",
                code_version=__version__,
                random_seed=result.seed,
                period_start=min(dates) if dates else None,
                period_end=max(dates) if dates else None,
                parameters={
                    "reference_origin": origin,
                    "reference_round_count": len(rounds),
                    "stake": result.stake,
                    "cashout_multiplier": result.cashout_multiplier,
                    "starting_bankroll": result.starting_bankroll,
                    "max_rounds": result.max_rounds,
                    "trials": result.trials,
                    "loss_limit": result.loss_limit,
                    "seed": result.seed,
                },
            )
            st.success("Résultats calculés; paramètres enregistrés dans SQLite.")
            cols = st.columns(4)
            cols[0].metric("Résultat net moyen", _format_number(result.mean_net_result))
            cols[1].metric("Médiane", _format_number(result.median_net_result))
            cols[2].metric("5e–95e percentile", f"{result.percentile_05_net:.2f} à {result.percentile_95_net:.2f}")
            cols[3].metric("Risque de ruine simulé", f"{result.ruin_probability:.1%}")
            cols = st.columns(3)
            cols[0].metric("Drawdown moyen", _format_number(result.mean_max_drawdown))
            cols[1].metric("Drawdown 95e percentile", _format_number(result.percentile_95_max_drawdown))
            cols[2].metric("Arrêt à la limite", f"{result.loss_limit_stop_probability:.1%}")
            result_frame = pd.DataFrame(
                [{"Essai": index, "Résultat net": item.net_result,
                  "Drawdown max.": item.max_drawdown, "Manches jouées": item.rounds_played,
                  "Ruine": item.ruin}
                 for index, item in enumerate(result.outcomes, start=1)]
            )
            st.plotly_chart(
                px.histogram(result_frame, x="Résultat net", title="Distribution des résultats simulés"),
                use_container_width=True,
            )
            with st.expander("Hypothèses et détails"):
                st.write(result.payout_assumption)
                st.write(result.independence_assumption)
                st.write(f"Graine : {result.seed} · Origine de référence : {ORIGIN_LABELS[origin]}")
                st.dataframe(result_frame, hide_index=True, use_container_width=True)
            st.caption("Ces résultats de simulation ne sont pas des résultats réels ni une promesse de gain.")
        except ValueError as exc:
            st.error(str(exc))

def _page_withdrawal(repository, origin: str, *, collection_status: dict | None = None) -> None:
    st.header("Assistant de mise et de retrait")
    st.caption(f"Origine sélectionnée : **{ORIGIN_LABELS[origin]}**")
    st.warning(
        "L'assistant utilise les multiplicateurs reçus pour indiquer si l'historique soutient une mise ou "
        "s'il vaut mieux s'abstenir. Son estimation est recalculée après une nouvelle manche enregistrée, "
        "mais ne garantit pas le prochain résultat. Il ne place jamais de mise réelle. "
        "Une fréquence passée élevée ne rend pas un objectif meilleur si les manches sont indépendantes."
    )
    rounds = repository.list_rounds(origin=origin)
    # SQLite returns rounds by insertion ID; keep that chronological order for the
    # out-of-sample check. Invalid rows remain in the recorded count, not the analysis.
    valid_rounds = [
        row for row in rounds
        if row.validation_status == "valid" and math.isfinite(float(row.multiplier))
        and float(row.multiplier) >= 1
    ]
    values = [float(row.multiplier) for row in valid_rounds]
    last_refresh = datetime.now().astimezone()
    latest = max(
        (row for row in valid_rounds if row.recorded_at is not None),
        key=lambda row: row.recorded_at,
        default=None,
    )
    latest_at = latest.recorded_at if latest else None
    if latest_at is not None:
        latest_utc = latest_at.replace(tzinfo=timezone.utc) if latest_at.tzinfo is None else latest_at.astimezone(timezone.utc)
        age_seconds = max(0.0, (datetime.now(timezone.utc) - latest_utc).total_seconds())
        latest_at_text = latest_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        age_seconds = None
        latest_at_text = "inconnue"

    study_fraction = 0.7
    minimum_rounds = minimum_rounds_for_evaluation(
        study_fraction=study_fraction,
        min_evaluation_rounds=MIN_EVALUATION_ROUNDS,
    )
    fresh = age_seconds is not None and age_seconds <= 24 * 60 * 60
    dated_rounds = [row.played_at for row in valid_rounds if row.played_at is not None]
    chronologically_ordered = all(
        earlier <= later for earlier, later in zip(dated_rounds, dated_rounds[1:])
    )
    reliable = (
        len(rounds) == len(valid_rounds)
        and len(dated_rounds) == len(valid_rounds)
        and chronologically_ordered
    )
    comparison = None
    if len(values) >= 2:
        try:
            comparison = compare_targets_out_of_sample(
                values, DEFAULT_TARGETS, stake=1.0, rtp=None, origin=origin,
                study_fraction=study_fraction, confidence_level=0.95, fee_per_round=None,
            )
        except ValueError:
            comparison = None

    st.subheader("Miser ou s’abstenir selon les multiplicateurs reçus")
    st.write(
        f"L'analyse utilise **{len(values)} multiplicateur(s) valide(s)** provenant de l'historique local "
        f"sélectionné ({ORIGIN_LABELS[origin]}). Le verdict ci-dessous s'appuie sur ces données et sur un "
        "contrôle chronologique hors échantillon."
    )
    if len(values) < minimum_rounds or not fresh or not reliable:
        decision = "DONNÉES INSUFFISANTES"
        if len(values) < minimum_rounds:
            study_n, evaluation_n, missing_evaluation_n, missing_total_n = _evaluation_counts(
                len(values), study_fraction
            )
            reason = (
                "DONNÉES INSUFFISANTES POUR VALIDER UNE STRATÉGIE PRÉDICTIVE. "
                f"La base contient {len(rounds)} manches enregistrées, dont {len(values)} valides. "
                f"La séparation chronologique 70/30 donne {study_n} manches d'étude et "
                f"{evaluation_n} manches d'évaluation; il en manque {missing_evaluation_n} "
                f"pour atteindre les {MIN_EVALUATION_ROUNDS} requises. Il faut {missing_total_n} "
                f"manches valides supplémentaires (minimum total : {minimum_rounds})."
            )
        elif not fresh:
            reason = "La date de collecte manque ou la dernière donnée date de plus de 24 heures."
        elif not reliable:
            reason = (
                "Données invalides, horodatages manquants ou ordre chronologique incohérent : "
                "l'historique n'est pas assez fiable pour valider une stratégie."
            )
        else:
            reason = "Données insuffisantes pour établir un avantage prédictif fiable."
    elif comparison and comparison.verdict != NO_ADVANTAGE:
        decision = "SIGNAL STATISTIQUE À ÉVALUER"
        reason = comparison.explanation
    else:
        decision = "NE PAS MISER"
        reason = (
            comparison.explanation if comparison else
            "L'analyse hors échantillon n'a pas établi d'avantage statistique fiable."
        )
    st.markdown(f"### **{decision}**")
    st.write(reason)
    category = _validation_category(len(values), comparison, fresh=fresh, reliable=reliable)
    st.write(f"État de validation statistique : **{category}**.")
    st.caption(
        "Un résultat validé désigne uniquement le test historique séparé; le verdict aide à lire "
        "les données, mais ne garantit pas le résultat de la prochaine manche."
    )
    if comparison and comparison.verdict != NO_ADVANTAGE:
        st.write("Indication : **SIGNAL HISTORIQUE À ÉVALUER** uniquement sur l'échantillon séparé; il ne prédit pas le prochain tour.")
    else:
        st.write("Indication : **ATTENDRE ET OBSERVER** · **AUCUN SIGNAL STATISTIQUE FIABLE**.")
    collection = _collection_state(collection_status, rounds, now=last_refresh)
    st.subheader("État de la collecte")
    state_message = f"{collection['state']} — {collection['cause']}"
    if collection["state"] == "COLLECTE ACTIVE":
        st.success(state_message)
    elif collection["state"] == "COLLECTE EN ATTENTE":
        st.info(state_message)
    elif collection["state"] == "COLLECTE EN ERREUR":
        st.error(state_message)
    else:
        st.warning(state_message)
    api_text = "oui" if collection["api_reachable"] is True else (
        "non" if collection["api_reachable"] is False else "état inconnu"
    )
    heartbeat_text = "récent" if collection["heartbeat_recent"] is True else (
        "ancien ou absent" if collection["heartbeat_recent"] is False else "état inconnu"
    )
    if collection["heartbeat_age"] is not None:
        heartbeat_text += f" ({collection['heartbeat_age']:.1f} s)"
    st.write(f"API locale joignable : **{api_text}** · heartbeat extension : **{heartbeat_text}**.")
    st.write(
        f"Manche effectivement présente dans SQLite : **{'oui' if latest else 'aucune manche valide'}** · "
        f"nouvelles manches valides enregistrées dans la fenêtre de {collection['freshness_window'].total_seconds() / 3600:g} h : "
        f"**{collection['recent_round_count']}**. Une présence en base prouve l'enregistrement local, "
        "pas la fin certifiée d'une manche ni sa provenance officielle."
    )
    st.caption(
        "La collecte s'appuie sur l'alignement de changements de l'historique affiché; une mutation du DOM "
        "ou un alignement technique ne certifie pas la fin officielle d'une manche."
    )
    if collection["last_error"] and collection["state"] != "COLLECTE EN ERREUR":
        st.caption(
            f"Dernière erreur API observée : {collection['last_error']} · un enregistrement a réussi depuis cette erreur."
        )
    latest_multiplier = f"{float(latest.multiplier):.2f}×" if latest else "inconnu"
    latest_played = latest.played_at if latest else None
    latest_played_text = latest_played.isoformat() if latest_played else "inconnue"
    st.write(
        f"Dernier multiplicateur enregistré (valide) : **{latest_multiplier}** · "
        f"heure de manche déclarée : **{latest_played_text}** · "
        f"enregistrée localement : **{latest_at_text}**."
    )
    st.write(
        f"Manches valides : **{len(values)}** sur {len(rounds)} ligne(s) · "
        f"Dernière actualisation des statistiques : **{last_refresh:%Y-%m-%d %H:%M:%S %Z}**."
    )
    study_n, evaluation_n, missing_evaluation_n, missing_total_n = _evaluation_counts(
        len(values), study_fraction
    )
    st.write(
        f"Contrôles statistiques : séparation chronologique 70/30 prédéfinie; "
        f"manches d'étude **{study_n}**, manches d'évaluation **{evaluation_n}/{MIN_EVALUATION_ROUNDS}**; "
        f"il en manque **{missing_evaluation_n}** pour atteindre le minimum d'évaluation, "
        f"soit **{missing_total_n}** manches valides supplémentaires au total."
    )
    st.caption(
        "Autres conditions : toutes les manches analysées doivent être valides, datées et dans l'ordre; "
        "le résultat doit ensuite être comparé hors échantillon à « ne pas miser », avec correction des comparaisons multiples. "
        + ("La qualité chronologique est vérifiée." if reliable else "La qualité chronologique n'est pas établie.")
        + (" Aucune conclusion prédictive n'est établie." if evaluation_n < MIN_EVALUATION_ROUNDS else "")
    )
    st.write(
        "Fraîcheur de la dernière donnée : **"
        f"{'récente (moins de 24 h)' if fresh else 'ancienne ou impossible à confirmer'}**."
    )
    reliability_text = (
        f"fiabilité structurelle {len(values)}/{len(rounds)} valides, horodatages cohérents; "
        "source de l’affichage Firefox non vérifiée"
        if reliable else
        f"fiabilité insuffisante : date manquante, ligne invalide ou ordre incohérent "
        f"({len(values)}/{len(rounds)} valides)"
    )
    if not fresh:
        reliability_text += "; dernière donnée ancienne ou absente"
    st.caption(f"Dernière donnée enregistrée : {latest_at_text} · {reliability_text}")
    surveillance = 25
    next_review = datetime.now().astimezone() + timedelta(days=1)
    remaining_for_validation = max(0, minimum_rounds - len(values))
    st.write(
        f"Prochaine réanalyse descriptive recommandée après **{surveillance} nouvelles manches valides** "
        f"ou au rappel du **{next_review:%d/%m/%Y}**. Pour disposer de l'échantillon d'évaluation prédéfini, "
        f"il manque **{remaining_for_validation} manches valides**. Cette échéance programme une revue; "
        "elle ne prédit ni le prochain multiplicateur ni l'arrivée d'une manche favorable."
    )
    thresholds = []
    estimate_window = 100
    recent_values = values[-estimate_window:]
    next_multiplier_estimate = next_round_median(values, window=estimate_window)
    next_interval = next_round_interval(values, window=estimate_window)
    backtest_points = rolling_median_backtest(values, window=estimate_window, limit=20)
    forecast_rows = [{
        "Manche cible": point.round_number,
        "Données utilisées avant cette manche": point.history_count,
        "Estimation avant la manche": f"{point.prediction:.2f}×",
        "Fourchette empirique 10–90 %": (
            f"{point.interval_low:.2f}–{point.interval_high:.2f}×"
            if point.interval_low is not None and point.interval_high is not None
            else f"indisponible (<{estimate_window} observations)"
        ),
        "Multiplicateur reçu": f"{point.actual:.2f}×",
        "Dans la fourchette": (
            "oui" if point.interval_low is not None and point.interval_high is not None
            and point.interval_low <= point.actual <= point.interval_high
            else "non" if point.interval_low is not None and point.interval_high is not None
            else "non évalué"
        ),
        "Écart reçu − estimé": f"{point.signed_error:+.2f}×",
        "Erreur absolue": f"{point.absolute_error:.2f}×",
        "Dernier résultat (référence)": f"{point.last_value_baseline:.2f}×",
        "Erreur absolue référence": f"{point.baseline_absolute_error:.2f}×",
    } for point in backtest_points]
    if next_multiplier_estimate is not None:
        forecast_rows.append({
            "Manche cible": "Prochaine (à venir)",
            "Données utilisées avant cette manche": len(recent_values),
            "Estimation avant la manche": f"{next_multiplier_estimate:.2f}×",
            "Fourchette empirique 10–90 %": (
                f"{next_interval[0]:.2f}–{next_interval[1]:.2f}×"
                if next_interval else f"indisponible (<{estimate_window} observations)"
            ),
            "Multiplicateur reçu": "en attente",
            "Dans la fourchette": "à vérifier après la manche",
            "Écart reçu − estimé": "—",
            "Erreur absolue": "—",
            "Dernier résultat (référence)": f"{recent_values[-1]:.2f}×",
            "Erreur absolue référence": "—",
        })
    st.subheader("Estimation probabiliste, recalculée après chaque manche")
    st.caption(
        f"À chaque nouvelle manche enregistrée, l'estimation centrale est recalculée avec la médiane des "
        f"{estimate_window} derniers multiplicateurs disponibles au maximum. La fourchette 10–90 % est affichée "
        f"seulement avec {estimate_window} observations antérieures; elle montre l'étendue descriptive de la fenêtre, "
        "pas une garantie de couverture. Le tableau montre aussi "
        "les estimations passées calculées sans utiliser le résultat de la manche cible. Le dernier résultat "
        "sert de référence simple pour vérifier si la médiane apporte une amélioration. "
        "La valeur peut rester identique si l'ajout d'une manche ne change pas la médiane."
    )
    st.dataframe(pd.DataFrame(forecast_rows), hide_index=True, use_container_width=True)
    scored_points = [point for point in backtest_points if point.history_count >= estimate_window]
    if len(scored_points) >= 10:
        model_mae = sum(point.absolute_error for point in scored_points) / len(scored_points)
        baseline_mae = sum(point.baseline_absolute_error for point in scored_points) / len(scored_points)
        st.write(
            f"Vérification chronologique sur {len(scored_points)} manches avec une fenêtre complète : "
            f"erreur absolue moyenne de la médiane **{model_mae:.2f}×**, contre **{baseline_mae:.2f}×** "
            "pour la référence du dernier multiplicateur."
        )
        if model_mae >= baseline_mae:
            st.warning(
                f"Sur cette période, la médiane des {estimate_window} derniers résultats "
                "ne fait pas mieux que la référence simple."
            )
        else:
            st.info(
                f"Sur cette période seulement, la médiane des {estimate_window} derniers résultats "
                "a une erreur moyenne plus faible que la référence simple."
            )
    else:
        st.info(
            f"La vérification de précision attend au moins 10 prévisions faites avec {estimate_window} manches antérieures; "
            f"il y en a actuellement {len(scored_points)}."
        )
    for target in WITHDRAWAL_REPORT_THRESHOLDS:
        if values:
            item = analyze_target(recent_values, target, stake=1.0, rtp=None, origin=origin)
            thresholds.append({
                "Multiplicateur central estimé (historique)": f"{next_multiplier_estimate:.2f}×",
                "Valeur d’arrêt / retrait": f"{target:.2f}×",
                "Fréquence historique d’atteinte": f"{item.frequency:.1%}",
                "IC Wilson 95 %": f"{item.ci_low:.1%} – {item.ci_high:.1%}",
                "Manches valides": item.n,
            })
        else:
            thresholds.append({
                "Multiplicateur central estimé (historique)": "indisponible",
                "Valeur d’arrêt / retrait": f"{target:.2f}×",
                "Fréquence historique d’atteinte": "indisponible",
                "IC Wilson 95 %": "—", "Manches valides": 0,
            })
    st.subheader("Scénarios de multiplicateur et seuil d’arrêt")
    if next_multiplier_estimate is not None:
        st.write(
            f"Valeur centrale historique pour la prochaine manche : **{next_multiplier_estimate:.2f}×** "
            f"(médiane des {len(recent_values)} derniers multiplicateurs valides, au maximum). Le tableau compare cette référence "
            "aux différents seuils d’arrêt et indique la fréquence historique de leur atteinte."
        )
    st.dataframe(pd.DataFrame(thresholds), hide_index=True, use_container_width=True)
    st.caption(
        "La médiane est une estimation centrale de l'historique, pas une prédiction certaine du prochain multiplicateur. "
        "La valeur d'arrêt est un seuil hypothétique choisi pour comparer les fréquences; elle n'est pas recommandée. "
        "Les intervalles de Wilson supposent des manches indépendantes et une fréquence stable."
    )
    if values:
        summary = descriptive_stats(values, origin=origin)
        stddev_text = (
            f"{summary.sample_std_dev:.2f}x"
            if summary.sample_std_dev is not None else "non calculable"
        )
        st.write(
            f"Descriptif historique : moyenne {summary.mean:.2f}x · médiane {summary.median:.2f}x · "
            f"écart-type d'échantillon {stddev_text} · n={summary.n_valid}. "
            "La moyenne est sensible aux valeurs extrêmes."
        )
        st.write(
            "Quantiles historiques : " + " · ".join(
                f"Q{int(probability * 100)}={value:.2f}x"
                for probability, value in summary.quantiles.items()
            ) + "."
        )
        if len(values) >= 4:
            split = len(values) // 2
            first_period, second_period = values[:split], values[split:]
            try:
                period_result = compare_periods(
                    first_period, second_period, origin=origin
                )
                st.write(
                    f"Comparaison chronologique descriptive : première moitié n={period_result.n_first}, "
                    f"médiane {period_result.median_first:.2f}x; seconde moitié n={period_result.n_second}, "
                    f"médiane {period_result.median_second:.2f}x; Mann–Whitney p={period_result.p_value:.3f}."
                )
                st.caption(period_result.limitation)
            except ValueError as exc:
                st.caption(f"Comparaison des périodes non calculable : {exc}")
        try:
            rng = autocorrelation_diagnostic(
                values, origin=origin, lags=min(10, len(values) - 1)
            )
            runs = runs_test(values, origin=origin)
            st.caption(
                f"Diagnostics exploratoires d'ordre : Ljung–Box (Holm jusqu'à {rng.lags[-1].lag} retards), "
                f"détection={rng.detected_after_correction}; test des séquences p={runs.p_value:.3f}, "
                f"détection à 5 %={runs.detected}. Une absence de détection n'établit pas l'indépendance."
            )
        except ValueError as exc:
            st.caption(f"Diagnostic RNG non calculable : {exc}")
    if comparison:
        chosen_result = next(row for row in comparison.rows if row.target == comparison.selected_target)
        st.write(
            f"Comparaison prédéfinie : objectif {comparison.selected_target:g}x choisi sur {comparison.study_n} "
            f"manches d'étude, puis {comparison.evaluation_n} manches ultérieures contre la référence "
            f"« ne pas miser » (espérance 0). Borne basse simultanée de l'espérance brute : "
            f"{chosen_result.evaluation_expectation_low:+.3f} par mise fictive; "
            f"IC family-wise à {comparison.confidence_level:.0%}, Bonferroni sur "
            f"{comparison.comparison_count} objectifs. "
            "Les frais réels ne sont pas documentés; ce résultat brut ne démontre pas une rentabilité nette."
        )
    if len(values) < 2:
        st.info("Au moins deux manches sont nécessaires pour cette page.")
        return

    st.subheader("1. Paramètres")
    columns = st.columns(3)
    stake = columns[0].number_input("Mise fictive de référence", min_value=0.01, value=1.0, step=0.5, key="wd_stake")
    confidence = columns[1].select_slider(
        "Niveau de confiance", options=[0.90, 0.95, 0.99], value=0.95,
        format_func=lambda x: f"{x:.0%}", key="wd_conf",
    )
    use_rtp = columns[2].checkbox(
        "Afficher un scénario RTP hypothétique (non vérifié)", value=False, key="wd_use_rtp"
    )
    rtp = None
    if use_rtp:
        rtp_percent = columns[2].number_input(
            "RTP hypothétique (%) — non vérifié pour Bet261", min_value=50.0, max_value=100.0,
            value=97.0, step=0.5, key="wd_rtp",
        )
        rtp = rtp_percent / 100
    chosen = st.multiselect(
        "Objectifs de retrait (x)", options=list(DEFAULT_TARGETS), default=list(DEFAULT_TARGETS), key="wd_targets"
    )
    columns = st.columns(2)
    add_custom = columns[0].checkbox("Ajouter un autre objectif", key="wd_add_custom")
    custom = columns[1].number_input(
        "Autre objectif (x)", min_value=1.01, value=10.0, step=0.5, key="wd_custom"
    ) if add_custom else None
    targets = sorted(set(chosen + ([float(custom)] if custom else [])))
    if not targets:
        st.info("Choisissez au moins un objectif de retrait.")
        return
    st.caption(
        "Modèle de calcul : gain net si réussite = mise × (x − 1) ; perte si échec = mise ; "
        "espérance = mise × (p × x − 1). Hypothèse de calcul, non confirmée par les règles du jeu."
    )

    st.subheader("2. Chaque objectif : observé, théorique, incertitude")
    try:
        analyses = [
            analyze_target(values, target, stake=stake, rtp=rtp, origin=origin, confidence_level=confidence)
            for target in targets
        ]
    except ValueError as exc:
        st.error(str(exc))
        return
    st.dataframe(
        pd.DataFrame([
            {
                "Objectif x": item.target,
                "Observations n": item.n,
                "Réussites": item.successes,
                "Fréquence historique": item.frequency,
                "Risque historique d'échec": 1.0 - item.frequency,
                f"IC bas ({confidence:.0%})": item.ci_low,
                f"IC haut ({confidence:.0%})": item.ci_high,
                "Probabilité théorique (hypothèse RTP)": item.theoretical_probability,
                "Gain net si réussite": item.net_gain_on_success,
                "Perte si échec": item.loss_on_failure,
                "Espérance théorique / manche": item.theoretical_expectation,
                "Espérance estimée (historique) / manche": item.empirical_expectation,
            }
            for item in analyses
        ]),
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Limites de l'estimation"):
        for limit in analyses[0].limits:
            st.write(f"- {limit}")
        st.write(
            "- Sous l'hypothèse d'un RTP inférieur à 100 %, l'espérance théorique est négative et identique pour "
            "tous les objectifs : changer d'objectif change la variance, pas l'espérance."
        )

    st.subheader("3. Comparaison avec séparation chronologique")
    st.caption(
        "Analyse exploratoire : les objectifs sont sélectionnés sur la période d'étude puis contrôlés sur la période suivante. "
        "Les intervalles de l'évaluation sont corrigés par Bonferroni. La référence est « ne pas miser » (espérance 0). "
        "Aucun frais ou barème vérifié n'est disponible : aucun résultat brut ne peut établir une rentabilité nette."
    )
    study_percent = st.slider("Part de la période d'étude (le début de l'historique)", 50, 90, 70, step=5, key="wd_split")
    try:
        comparison = compare_targets_out_of_sample(
            values, targets, stake=stake, rtp=rtp, origin=origin,
            study_fraction=study_percent / 100, confidence_level=confidence, fee_per_round=None,
        )
    except ValueError as exc:
        st.error(str(exc))
        return
    if comparison.verdict == NO_ADVANTAGE:
        st.error(f"**{comparison.verdict}**")
    else:
        st.warning(f"**{comparison.verdict}**")
    st.write(comparison.explanation)
    st.dataframe(
        pd.DataFrame([
            {
                "Objectif x": row.target,
                "Fréquence (étude)": row.study_frequency,
                "Espérance (étude)": row.study_expectation,
                "Fréquence (évaluation)": row.evaluation_frequency,
                f"IC simultané bas (Bonferroni, famille {comparison.comparison_count}, {confidence:.0%})": row.evaluation_ci_low,
                f"IC simultané haut (Bonferroni, famille {comparison.comparison_count}, {confidence:.0%})": row.evaluation_ci_high,
                "Espérance (évaluation)": row.evaluation_expectation,
                "Borne basse espérance (évaluation)": row.evaluation_expectation_low,
                "Référence sans mise": comparison.no_bet_reference_expectation,
                "Variance des retours évalués": row.evaluation_net_variance,
            }
            for row in comparison.rows
        ]),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        f"Étude : {comparison.study_n} manches · évaluation : {comparison.evaluation_n} manches suivantes, "
        "jamais utilisées pour choisir. Les séries récentes ne servent pas à prédire la prochaine manche."
    )

    st.subheader("4. Simulation Monte-Carlo à argent fictif")
    columns = st.columns(3)
    bankroll = columns[0].number_input("Montant initial fictif", min_value=0.01, value=100.0, step=10.0, key="wd_bank")
    sim_stake = columns[1].number_input("Mise fixe fictive", min_value=0.01, value=1.0, step=0.5, key="wd_sim_stake")
    sim_rounds = columns[2].number_input("Nombre de manches", min_value=1, value=200, step=50, key="wd_rounds")
    columns = st.columns(3)
    sim_trials = columns[0].number_input("Nombre de simulations", min_value=1, value=1000, step=100, key="wd_trials")
    sim_seed = columns[1].number_input("Graine aléatoire", min_value=0, value=42, step=1, key="wd_seed")
    model_options = ["Probabilité = fréquence historique"] + (["Probabilité = RTP / x (hypothèse)"] if rtp else [])
    model = columns[2].selectbox("Modèle de probabilité", model_options, key="wd_model")
    if st.button("Lancer la simulation pour chaque objectif", key="wd_run"):
        results = []
        try:
            for item in analyses:
                use_theory = model != model_options[0]
                probability = item.theoretical_probability if use_theory else item.frequency
                results.append(simulate_fixed_target(
                    success_probability=probability, stake=sim_stake, target=item.target,
                    starting_bankroll=bankroll, rounds=int(sim_rounds), trials=int(sim_trials),
                    seed=int(sim_seed), model=model,
                ))
            repository.record_analysis(
                analysis_type="withdrawal_monte_carlo",
                code_version=__version__,
                random_seed=int(sim_seed),
                parameters={
                    "origin": origin, "round_count": len(values), "targets": targets, "model": model,
                    "stake": sim_stake, "starting_bankroll": bankroll, "rounds": int(sim_rounds),
                    "trials": int(sim_trials), "rtp": rtp,
                },
            )
            st.session_state["wd_results"] = results
        except ValueError as exc:
            st.session_state.pop("wd_results", None)
            st.error(str(exc))
    results = st.session_state.get("wd_results")
    if results:
        by_target = {item.target: item for item in analyses}
        st.dataframe(
            pd.DataFrame([
                {
                    "Objectif x": sim.target,
                    "Fréquence historique": by_target[sim.target].frequency if sim.target in by_target else None,
                    "Probabilité théorique": by_target[sim.target].theoretical_probability if sim.target in by_target else None,
                    "IC historique": (
                        f"{by_target[sim.target].ci_low:.1%} – {by_target[sim.target].ci_high:.1%}"
                        if sim.target in by_target else None
                    ),
                    "Solde final moyen": sim.mean_final_bankroll,
                    "Solde final 5e–95e": f"{sim.final_bankroll_p05:.1f} – {sim.final_bankroll_p95:.1f}",
                    "Taux de réussite moyen": sim.mean_success_rate,
                    "Pertes cumulées moyennes": sim.mean_cumulative_losses,
                    "Drawdown max. moyen": sim.mean_max_drawdown,
                    "Risque de ruine simulé": sim.ruin_probability,
                }
                for sim in results
            ]),
            hide_index=True,
            use_container_width=True,
        )
        shown = st.selectbox("Distribution du solde final pour l'objectif", [sim.target for sim in results], key="wd_shown")
        detail = next(sim for sim in results if sim.target == shown)
        st.plotly_chart(
            px.histogram(pd.DataFrame({"Solde final": detail.final_bankrolls}), x="Solde final",
                         title=f"Soldes finaux simulés — objectif {shown:g}x"),
            use_container_width=True,
        )
        st.caption(detail.assumption)
        st.caption("Simulation à argent fictif : ce n'est ni un résultat réel ni une promesse de gain.")

def _page_quality(repository) -> None:
    st.header("Qualité des données")
    for origin in ORIGINS:
        rounds = repository.list_rounds(origin=origin)
        bets = repository.list_bets(origin=origin)
        st.subheader(ORIGIN_LABELS[origin])
        columns = st.columns(4)
        columns[0].metric("Manches", len(rounds))
        columns[1].metric("Multiplicateurs invalides en base", sum(row.validation_status != "valid" for row in rounds))
        columns[2].metric("Manches sans date", sum(row.played_at is None for row in rounds))
        columns[3].metric("Manches sans ID externe", sum(row.external_id is None for row in rounds))
        missing_bet_fields = {
            "Mise absente": sum(row.stake_amount is None for row in bets),
            "Retour absent": sum(row.returned_amount is None for row in bets),
            "Net absent": sum(row.net_amount is None for row in bets),
            "Date absente": sum(row.bet_at is None for row in bets),
        }
        st.write(f"Paris personnels : {len(bets)}")
        if bets:
            st.dataframe(
                pd.DataFrame([{"Champ manquant": key, "Nombre de paris": value}
                              for key, value in missing_bet_fields.items()]),
                hide_index=True,
                use_container_width=True,
            )
    st.info(
        "Les doublons d'identifiants externes et les valeurs invalides sont refusés à l'enregistrement. "
        "Les lignes rejetées pendant un import sont rapportées immédiatement dans la page d'import."
    )


def _page_settings(repository) -> None:
    st.header("Paramètres et exportations")
    st.write(f"Version du code : **{__version__}**")
    st.write(f"Base locale : `{config.DB_PATH}`")
    st.write(f"RTP théorique : **désactivé** ({config.HYPOTHETICAL_RTP})")
    st.caption("Aucune API distante, aucun cookie et aucun identifiant de connexion ne sont configurés.")
    for origin in ORIGINS:
        st.subheader(f"Exporter les données {ORIGIN_LABELS[origin].lower()}")
        rounds = repository.list_rounds(origin=origin)
        bets = repository.list_bets(origin=origin)
        if rounds:
            st.download_button(
                f"Télécharger les manches {origin} (CSV)",
                data=_round_frame(rounds).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"manches_{origin}.csv",
                mime="text/csv",
                key=f"round_export_{origin}",
            )
        if bets:
            st.download_button(
                f"Télécharger les paris {origin} (CSV)",
                data=_bet_frame(bets).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"paris_{origin}.csv",
                mime="text/csv",
                key=f"bet_export_{origin}",
            )
    analyses = repository.list_analyses()
    st.subheader("Historique des analyses enregistrées")
    if analyses:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Date": row.created_at,
                        "Type": row.analysis_type,
                        "Période début": row.period_start,
                        "Période fin": row.period_end,
                        "Version": row.code_version,
                        "Graine": row.random_seed,
                        "Paramètres": row.parameters,
                    }
                    for row in analyses
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("Aucune simulation ou analyse enregistrée pour le moment.")


def main() -> None:
    st.set_page_config(page_title="Aviator Intelligence Lab — Bet261", layout="wide")
    st.title("Aviator Intelligence Lab — Bet261")
    st.caption("Laboratoire statistique local · aucune décision de pari automatisée")
    try:
        repository = get_repository()
    except Exception as exc:  # Show local database errors instead of a blank Streamlit traceback.
        st.error(f"Impossible d'ouvrir la base SQLite : {exc}")
        st.stop()

    with st.sidebar:
        st.header("Navigation")
        page = st.selectbox("Page", PAGES)
        origin = st.selectbox(
            "Origine des données à analyser",
            ORIGINS,
            format_func=lambda value: ORIGIN_LABELS[value],
        )
        st.caption("La sélection d'origine filtre les analyses et évite de mélanger réel et simulé.")

    _render_live_content(repository, page, origin)


if __name__ == "__main__":
    main()
