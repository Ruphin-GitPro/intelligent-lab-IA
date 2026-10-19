"""Tableau de bord local en français pour le laboratoire Aviator."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import tempfile
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import streamlit as st

from aviator_lab import __version__, config
from aviator_lab.analytics import descriptive_stats, financial_summary
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
    "Analyses statistiques",
    "Mes résultats financiers",
    "Simulations fictives",
    "Qualité des données",
    "Collecte Firefox",
    "Paramètres et exportations",
)


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
            return json.loads(response.read(16_384))
    except (OSError, URLError, TimeoutError, ValueError):
        return None


def _page_collection(repository) -> None:
    st.header("Collecte Firefox Bet261")
    st.warning("Collecte Bet261 non vérifiée — aucune source autorisée et fiable n'est connectée.")
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
        st.caption("La version actuelle est en diagnostic DOM seulement; elle n'extrait aucun résultat.")
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
            "Ces champs décrivent la dernière ligne stockée; ils ne constituent pas une observation Bet261 vérifiée."
        )
        st.dataframe(_round_frame(rounds[-20:][::-1]), hide_index=True, use_container_width=True)
    else:
        st.info("Aucun résultat réel enregistré dans SQLite.")


@st.fragment(run_every=5)
def _render_live_content(repository, page: str, origin: str) -> None:
    """Rafraîchit l'état et la vue courante sans recharger toute l'application."""
    st.warning("Collecte Bet261 non vérifiée — aucune source autorisée et fiable n'est connectée.")
    status = _local_collection_status()
    if status is None:
        st.error("Collecte locale : API hors ligne")
    elif status.get("collector_connected"):
        st.success(
            f"Extension reliée (diagnostic seulement) · {status.get('round_count', 0)} ligne(s) d'origine real en SQLite"
        )
    else:
        st.info(f"API disponible, heartbeat absent · {status.get('round_count', 0)} ligne(s) en SQLite")
    if status and status.get("last_collection_error"):
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
    elif page == "Analyses statistiques":
        _page_advanced(repository, origin)
    elif page == "Mes résultats financiers":
        _page_finances(repository, origin)
    elif page == "Simulations fictives":
        _page_simulations(repository, origin)
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
