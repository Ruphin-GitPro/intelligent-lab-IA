from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from aviator_lab import config
from aviator_lab.dashboard.app import _decision_status, _temporal_frequency_frame, get_repository
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


def test_dashboard_pages_open_with_an_empty_local_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dashboard.sqlite")
    get_repository.clear()
    app_path = Path(__file__).parents[1] / "src" / "aviator_lab" / "dashboard" / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    assert not app.exception
    assert any("Collecte Bet261 non vérifiée" in warning.value for warning in app.warning)

    for page in (
        "Historique des manches",
        "Distribution des multiplicateurs",
        "Probabilités empiriques",
        "Analyses statistiques",
        "Mes résultats financiers",
        "Simulations fictives",
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
