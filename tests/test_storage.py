from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from aviator_lab.storage import (
    DuplicateRoundError,
    RoundRepository,
    ValidationError,
    validate_multiplier,
)


@pytest.fixture
def repository(tmp_path):
    repo = RoundRepository(tmp_path / "test.sqlite")
    repo.initialize()
    yield repo
    repo.close()


@pytest.mark.parametrize("value", [1, 1.2, "2.50", Decimal("3.25")])
def test_multiplier_accepts_finite_values_at_least_one(value):
    assert validate_multiplier(value) >= Decimal("1")


@pytest.mark.parametrize(
    "value", [None, True, "abc", 0, -1, float("nan"), float("inf"), "1.00001"]
)
def test_multiplier_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        validate_multiplier(value)


def test_round_is_saved_and_can_be_read(repository):
    saved = repository.add_round(
        "2.35", source="saisie manuelle", external_id="r-1", origin="real"
    )

    loaded = repository.list_rounds(origin="real")
    assert saved.id is not None
    assert len(loaded) == 1
    assert loaded[0].multiplier == Decimal("2.3500")
    assert loaded[0].validation_status == "valid"
    assert loaded[0].recorded_at is not None


def test_round_survives_reopening_the_sqlite_database(tmp_path):
    database_path = tmp_path / "persistent.sqlite"
    first = RoundRepository(database_path)
    first.initialize()
    first.add_round("4.25", source="manuel", external_id="persist-1")
    first.close()

    reopened = RoundRepository(database_path)
    reopened.initialize()
    try:
        records = reopened.list_rounds()
        assert len(records) == 1
        assert records[0].external_id == "persist-1"
        assert records[0].multiplier == Decimal("4.2500")
        assert records[0].recorded_at is not None
    finally:
        reopened.close()


def test_synthetic_storage_smoke_is_read_back_from_the_exact_test_database(tmp_path):
    database_path = (tmp_path / "sqlite-smoke" / "aviator_test.sqlite").resolve()
    repository = RoundRepository(database_path)
    repository.initialize()
    try:
        repository.add_round(
            "2.375",
            source="automated SQLite smoke test",
            origin="synthetic",
            external_id="synthetic-sqlite-smoke-1",
        )
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT multiplier, source, origin FROM rounds WHERE external_id = ?",
                ("synthetic-sqlite-smoke-1",),
            ).fetchone()
        assert row == (2.375, "automated SQLite smoke test", "synthetic")
    finally:
        repository.close()


def test_initialize_adds_recorded_at_without_rewriting_existing_rounds(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """CREATE TABLE rounds (
            id INTEGER PRIMARY KEY,
            external_id VARCHAR(200) UNIQUE,
            multiplier NUMERIC NOT NULL,
            played_at DATETIME,
            source VARCHAR(200) NOT NULL,
            origin VARCHAR(20) NOT NULL,
            validation_status VARCHAR(20) NOT NULL,
            invalid_reason VARCHAR(500)
        )"""
    )
    connection.execute(
        "INSERT INTO rounds VALUES (1, 'legacy-1', 2.5, NULL, 'old import', 'real', 'valid', NULL)"
    )
    connection.commit()
    connection.close()

    repository = RoundRepository(database_path)
    repository.initialize()
    try:
        rounds = repository.list_rounds()
        assert len(rounds) == 1
        assert rounds[0].external_id == "legacy-1"
        assert rounds[0].recorded_at is None
        added = repository.add_round(3.0, source="test", external_id="new-1")
        assert added.recorded_at is not None
    finally:
        repository.close()


def test_duplicate_external_id_is_reported(repository):
    repository.add_round(1.5, source="csv", external_id="r-1")

    with pytest.raises(DuplicateRoundError):
        repository.add_round(2.0, source="csv", external_id="r-1")

    assert len(repository.list_rounds()) == 1


def test_synthetic_and_real_rows_are_separately_filterable(repository):
    repository.add_round(1.5, source="manual", origin="real")
    repository.add_round(2.5, source="jeu de test", origin="synthetic")

    assert [row.origin for row in repository.list_rounds(origin="real")] == ["real"]
    assert [row.origin for row in repository.list_rounds(origin="synthetic")] == [
        "synthetic"
    ]


def test_source_and_origin_are_validated(repository):
    with pytest.raises(ValidationError):
        repository.add_round(2, source=" ")
    with pytest.raises(ValidationError):
        repository.add_round(2, source="manual", origin="import")


def test_round_and_bet_timestamps_must_be_parsed_datetimes(repository):
    with pytest.raises(ValidationError, match="horodatage"):
        repository.add_round(2, source="manual", played_at="yesterday")
    with pytest.raises(ValidationError, match="horodatage"):
        repository.add_round(2, source="manual", played_at=date(2026, 10, 18))
    with pytest.raises(ValidationError, match="horodatage"):
        repository.add_bet(source="manual", stake_amount=1, bet_at="not-a-date")


def test_analysis_parameters_and_seed_are_saved(repository):
    analysis = repository.record_analysis(
        analysis_type="simulation",
        code_version="0.1.0",
        parameters={"stake": 10, "trials": 100},
        random_seed=42,
    )

    loaded = repository.list_analyses()
    assert analysis.id is not None
    assert loaded[0].parameters == {"stake": 10, "trials": 100}
    assert loaded[0].random_seed == 42
    assert loaded[0].code_version == "0.1.0"


def test_analysis_rejects_invalid_seed(repository):
    with pytest.raises(ValidationError, match="graine"):
        repository.record_analysis(
            analysis_type="simulation",
            code_version="0.1.0",
            parameters={},
            random_seed=-1,
        )
