from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from aviator_lab import config
from aviator_lab.analytics.withdrawal import NO_ADVANTAGE
from aviator_lab.dashboard.app import (
    _collection_state,
    _decision_status,
    _evaluation_counts,
    _temporal_frequency_frame,
    _validation_category,
    get_repository,
)
from aviator_lab.storage import RoundRepository


def test_temporal_frequency_groups_dated_rounds_and_excludes_missing_dates():
    rounds = [
        SimpleNamespace(played_at=datetime(2026, 1, 2, 10), multiplier=2.5),
        SimpleNamespace(played_at=datetime(2026, 1, 2, 11), multiplier=1.5),
        SimpleNamespace(played_at=datetime(2026, 1, 3), multiplier=3.0),
        SimpleNamespace(played_at=None, multiplier=9.0),
    ]

    result = _temporal_frequency_frame(rounds, 2.0)

    assert result["Période"].tolist() == [datetime(2026, 1, 2).date(), datetime(2026, 1, 3).date()]
    assert result["Manches datées"].tolist() == [2, 1]
    assert result["Fréquence observée ≥ seuil"].tolist() == [0.5, 1.0]


def test_decision_helper_abstains_when_sample_is_small_or_no_edge_is_established():
    assert _decision_status([1.5] * 99, origin="real")[0] == "DONNÉES INSUFFISANTES"
    status, explanation = _decision_status([1.5] * 120, origin="real")
    assert status == "AUCUN SIGNAL FIABLE — NE PAS MISER"
    assert "provenance Bet261 n'est pas vérifiée" in explanation


def test_withdrawal_validation_counts_preserve_chronological_70_30_minimum():
    assert _evaluation_counts(215) == (150, 65, 135, 449)
    assert _evaluation_counts(237) == (165, 72, 128, 427)
    assert _evaluation_counts(664) == (464, 200, 0, 0)


def test_withdrawal_validation_categories_do_not_promote_small_samples():
    no_advantage = SimpleNamespace(
        verdict=NO_ADVANTAGE,
        selected_target=2.0,
        rows=[SimpleNamespace(target=2.0, evaluation_expectation_low=-0.1)],
    )
    assert _validation_category(237, no_advantage, fresh=True, reliable=True) == "DONNÉES INSUFFISANTES"
    assert _validation_category(664, no_advantage, fresh=True, reliable=True) == "AUCUN AVANTAGE STATISTIQUE DÉMONTRÉ"

    promising = SimpleNamespace(
        verdict=NO_ADVANTAGE,
        selected_target=2.0,
        rows=[SimpleNamespace(target=2.0, evaluation_expectation_low=0.01)],
    )
    assert _validation_category(664, promising, fresh=True, reliable=True) == "RÉSULTAT PROMETTEUR MAIS NON VALIDÉ"

    validated = SimpleNamespace(verdict="POSITIVE_GAP", rows=[])
    assert _validation_category(664, validated, fresh=True, reliable=True) == "RÉSULTAT VALIDÉ SUR L'ÉCHANTILLON DE TEST"


def test_dashboard_pages_open_with_an_empty_local_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dashboard.sqlite")
    get_repository.clear()
    app_path = Path(__file__).parents[1] / "src" / "aviator_lab" / "dashboard" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    assert any("Collecte Bet261 non vérifiée" in warning.value for warning in app.warning)

    app.selectbox[0].select("Assistant de mise et de retrait").run()
    assert not app.exception
    assert any("Miser ou s’abstenir selon les multiplicateurs reçus" in item.value for item in app.subheader)
    assert any("Estimation probabiliste, recalculée après chaque manche" in item.value for item in app.subheader)
    assert any("DONNÉES INSUFFISANTES" in item.value for item in app.markdown)
    assert any("ATTENDRE ET OBSERVER" in item.value for item in app.markdown)

    for page in (
        "Historique des manches",
        "Distribution des multiplicateurs",
        "Probabilités empiriques",
        "Analyses statistiques",
        "Mes résultats financiers",
        "Simulations fictives",
        "Assistant de mise et de retrait",
        "Qualité des données",
        "Collecte Firefox",
        "Paramètres et exportations",
    ):
        app.selectbox[0].select(page).run()
        assert not app.exception, f"Erreur dans la page {page}: {app.exception}"



def test_dashboard_navigates_populated_distribution_and_finances_views(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "populated-dashboard.sqlite"
    monkeypatch.setattr(config, "DB_PATH", database_path)
    repository = RoundRepository(database_path)
    repository.initialize()
    repository.add_round(1.5, source="import:test.csv", external_id="round-1",
                         played_at=datetime(2026, 1, 2, 10))
    repository.add_round(3.0, source="import:test.csv", external_id="round-2",
                         played_at=datetime(2026, 1, 2, 11))
    repository.add_bet(
        source="saisie manuelle",
        external_id="bet-1",
        stake_amount=5,
        returned_amount=8,
        currency="MGA",
        bet_at=datetime(2026, 1, 2, 12),
    )
    repository.close()

    get_repository.clear()
    app_path = Path(__file__).parents[1] / "src" / "aviator_lab" / "dashboard" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    app.selectbox[0].select("Distribution des multiplicateurs").run()
    assert not app.exception
    assert app.selectbox[0].value == "Distribution des multiplicateurs"

    app.selectbox[0].select("Mes résultats financiers").run()
    assert not app.exception
    assert app.selectbox[0].value == "Mes résultats financiers"

def test_withdrawal_assistant_opens_with_synthetic_looking_rounds_and_shows_no_advantage(
    tmp_path, monkeypatch
):
    import numpy as np

    database_path = tmp_path / "withdrawal-dashboard.sqlite"
    monkeypatch.setattr(config, "DB_PATH", database_path)
    repository = RoundRepository(database_path)
    repository.initialize()
    uniform = np.random.default_rng(5).random(110)  # données générées par le test, jamais issues de Bet261
    for index, draw in enumerate(uniform):
        repository.add_round(
            round(float(max(1.0, 0.97 / max(draw, 1e-9))), 2),
            source="test-generated", external_id=f"wd-{index}", played_at=datetime(2026, 1, 2, 10),
        )
    repository.close()

    get_repository.clear()
    import streamlit as st

    st.cache_resource.clear()  # vide aussi le cache de la copie du script exécutée par AppTest
    app_path = Path(__file__).parents[1] / "src" / "aviator_lab" / "dashboard" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    app.selectbox[0].select("Assistant de mise et de retrait").run()
    assert not app.exception, app.exception
    assert any("DONNÉES INSUFFISANTES" in item.value for item in app.markdown)
    assert any("DONNÉES INSUFFISANTES POUR VALIDER UNE STRATÉGIE PRÉDICTIVE" in item.value for item in app.markdown)
    assert any("554 manches valides" in item.value for item in app.markdown)
    assert any("25 nouvelles manches valides" in item.value for item in app.markdown)
    assert any("manches d'évaluation **33/200**" in item.value for item in app.markdown)
    assert any("il en manque **167**" in item.value for item in app.markdown)
    assert any("manches d'étude **77**" in item.value for item in app.markdown)
    assert any("554 manches valides supplémentaires" in item.value for item in app.markdown)
    assert any("Quantiles historiques" in item.value for item in app.markdown)
    assert any("Comparaison chronologique descriptive" in item.value for item in app.markdown)
    assert any("ne garantit pas le résultat de la prochaine manche" in item.value for item in app.caption)
    assert any("AVANTAGE NON DÉMONTRÉ" in item.value for item in app.error)


def _withdrawal_dashboard(tmp_path, monkeypatch, database_name="live-withdrawal.sqlite"):
    database_path = tmp_path / database_name
    monkeypatch.setattr(config, "DB_PATH", database_path)
    repository = RoundRepository(database_path)
    repository.initialize()
    get_repository.clear()
    import streamlit as st

    st.cache_resource.clear()
    app_path = Path(__file__).parents[1] / "src" / "aviator_lab" / "dashboard" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    app.selectbox[0].select("Assistant de mise et de retrait").run()
    assert not app.exception, app.exception
    return app, repository


def test_withdrawal_assistant_shows_data_refresh_and_distinct_collection_signals(
    tmp_path, monkeypatch
):
    app, repository = _withdrawal_dashboard(tmp_path, monkeypatch)
    played_at = datetime.now(timezone.utc).replace(tzinfo=None)
    repository.add_round(
        2.75, source="test", external_id="live-round-1", played_at=played_at
    )
    app.run()

    displayed = "\n".join(item.value for item in app.markdown)
    assert "2.75×" in displayed
    assert "Manches valides : **1** sur 1 ligne(s)" in displayed
    assert "Dernière actualisation des statistiques" in displayed
    assert "API locale joignable" in displayed
    assert "heartbeat extension" in displayed
    assert "Manche effectivement présente dans SQLite : **oui**" in displayed
    assert "NE PAS MISER" in displayed or "DONNÉES INSUFFISANTES" in displayed
    assert not app.exception, app.exception
    repository.close()


def test_withdrawal_assistant_refreshes_from_sqlite_without_double_counting(
    tmp_path, monkeypatch
):
    app, repository = _withdrawal_dashboard(tmp_path, monkeypatch, "refresh.sqlite")
    repository.add_round(
        1.5, source="test", external_id="refresh-1", played_at=datetime.now()
    )
    app.run()
    assert any("Manches valides : **1** sur 1 ligne(s)" in item.value for item in app.markdown)

    repository.add_round(
        3.0, source="test", external_id="refresh-2", played_at=datetime.now()
    )
    app.run()
    displayed = "\n".join(item.value for item in app.markdown)
    assert "Manches valides : **2** sur 2 ligne(s)" in displayed
    assert len(repository.list_rounds(origin="real")) == 2
    repository.close()


def test_withdrawal_assistant_marks_old_data_and_unconfirmed_collection(
    tmp_path, monkeypatch
):
    from urllib.error import URLError

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("test API indisponible")),
    )
    app, repository = _withdrawal_dashboard(tmp_path, monkeypatch, "stale.sqlite")
    repository.add_round(
        2.0, source="test", external_id="stale-1", played_at=datetime.now() - timedelta(days=3)
    )
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE rounds SET recorded_at = ? WHERE external_id = ?",
            ((datetime.now() - timedelta(days=3)).isoformat(sep=" "), "stale-1"),
        )
    app.run()

    displayed = "\n".join(item.value for item in app.markdown)
    assert "ancienne ou impossible à confirmer" in displayed
    assert "API locale joignable : **non**" in displayed
    assert "heartbeat extension : **ancien ou absent**" in displayed
    assert "Manche effectivement présente dans SQLite : **oui**" in displayed
    assert not app.exception, app.exception
    repository.close()


def _collection_api_status(*, heartbeat_age=12, connected=True, error=None):
    return {
        "ok": True,
        "collector_connected": connected,
        "last_heartbeat_age_seconds": heartbeat_age,
        "last_collection_error": error,
        "last_collection_error_at": None,
        "history_ingest": {"last_saved_at": None},
    }


def _round(
    *, recorded_at, multiplier=2.0, validation_status="valid", source="firefox_history_window_v1"
):
    return SimpleNamespace(
        recorded_at=recorded_at,
        played_at=recorded_at,
        multiplier=multiplier,
        validation_status=validation_status,
        source=source,
    )


def test_collection_active_requires_recent_heartbeat_and_new_valid_sqlite_round():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    state = _collection_state(
        _collection_api_status(),
        [_round(recorded_at=now - timedelta(minutes=2))],
        now=now,
    )

    assert state["state"] == "COLLECTE ACTIVE"
    assert state["api_reachable"] is True
    assert state["heartbeat_recent"] is True
    assert state["recent_round_count"] == 1


def test_assistant_can_show_collection_active_while_statistical_decision_waits(
    tmp_path, monkeypatch
):
    import json

    now = datetime.now(timezone.utc)
    api_status = _collection_api_status()
    api_status["history_ingest"]["last_saved_at"] = now.isoformat()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _limit):
            return json.dumps(api_status).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    app, repository = _withdrawal_dashboard(tmp_path, monkeypatch, "active-dashboard.sqlite")
    repository.add_round(
        2.35,
        source="firefox_history_window_v1",
        external_id="active-firefox-round",
        played_at=now.replace(tzinfo=None),
    )
    app.run()

    assert any("COLLECTE ACTIVE" in item.value for item in app.success)
    assert any("ATTENDRE ET OBSERVER" in item.value for item in app.markdown)
    assert any("DONNÉES INSUFFISANTES" in item.value for item in app.markdown)
    assert not app.exception, app.exception
    repository.close()


def test_collection_waiting_does_not_treat_recent_heartbeat_as_a_saved_round():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    state = _collection_state(_collection_api_status(), [], now=now)

    assert state["state"] == "COLLECTE EN ATTENTE"
    assert state["recent_round_count"] == 0
    assert "sans qu'une nouvelle manche" in state["cause"]


def test_collection_error_when_api_is_unreachable_or_heartbeat_is_expired():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    unreachable = _collection_state(None, [], now=now)
    expired = _collection_state(
        _collection_api_status(heartbeat_age=91, connected=True), [], now=now
    )

    assert unreachable["state"] == "COLLECTE EN ERREUR"
    assert unreachable["api_reachable"] is False
    assert expired["state"] == "COLLECTE EN ERREUR"
    assert "91.0 s" in expired["cause"]


def test_collection_waiting_for_old_data_and_ignores_recent_invalid_rows():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    rounds = [
        _round(recorded_at=now - timedelta(days=2)),
        _round(recorded_at=now - timedelta(minutes=1), validation_status="invalid"),
    ]

    state = _collection_state(_collection_api_status(), rounds, now=now)

    assert state["state"] == "COLLECTE EN ATTENTE"
    assert state["recent_round_count"] == 0
    assert state["valid_round_count"] == 1


def test_recent_manually_imported_round_does_not_prove_firefox_collection_active():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    state = _collection_state(
        _collection_api_status(),
        [_round(recorded_at=now - timedelta(minutes=1), source="import:history.csv")],
        now=now,
    )

    assert state["state"] == "COLLECTE EN ATTENTE"
    assert state["recent_round_count"] == 0


def test_collection_state_is_unknown_when_heartbeat_or_timestamps_are_unavailable():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    missing_heartbeat = _collection_state(
        {"ok": True, "collector_connected": None}, [], now=now
    )
    missing_timestamp = _collection_state(
        _collection_api_status(), [_round(recorded_at=None)], now=now
    )
    unreadable_status = _collection_state(
        {"_api_reachable": True, "_status_valid": False, "_status_error": "JSON invalide"},
        [], now=now,
    )

    assert missing_heartbeat["state"] == "ÉTAT INCONNU"
    assert missing_timestamp["state"] == "ÉTAT INCONNU"
    assert unreadable_status["state"] == "ÉTAT INCONNU"
    assert unreadable_status["api_reachable"] is True


def test_collection_reports_observed_unresolved_error_cause():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    status = _collection_api_status(error="SQLite write failed")
    status["last_collection_error_at"] = (now - timedelta(minutes=1)).isoformat()
    status["history_ingest"]["last_saved_at"] = (now - timedelta(minutes=2)).isoformat()

    state = _collection_state(status, [], now=now)

    assert state["state"] == "COLLECTE EN ERREUR"
    assert "SQLite write failed" in state["cause"]
