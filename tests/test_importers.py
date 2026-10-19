from datetime import datetime

from openpyxl import Workbook
import pytest

from aviator_lab.ingestion import ImportFileError, import_bets, import_rounds
from aviator_lab.storage import RoundRepository


@pytest.fixture
def repository(tmp_path):
    repo = RoundRepository(tmp_path / "import-test.sqlite")
    repo.initialize()
    yield repo
    repo.close()


def test_csv_import_saves_valid_rows_and_reports_invalid_ones(repository, tmp_path):
    path = tmp_path / "rounds.csv"
    path.write_text(
        "multiplier,external_id,played_at\n"
        "1.25,a,2026-10-01T12:30:00\n"
        "not-a-number,b,\n"
        ",c,\n"
        "2.50,a,\n",
        encoding="utf-8",
    )

    report = import_rounds(path, repository)

    assert report.imported == 1
    assert [issue.row_number for issue in report.rejected] == [3, 4, 5]
    assert "existe déjà" in report.rejected[2].message
    saved = repository.list_rounds()
    assert saved[0].multiplier == 1.25
    assert saved[0].played_at == datetime(2026, 10, 1, 12, 30)


def test_excel_import_reads_columns_origin_and_datetime(repository, tmp_path):
    path = tmp_path / "rounds.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["multiplier", "external_id", "played_at", "origin"])
    sheet.append([3.5, "excel-1", datetime(2026, 10, 2, 9), "synthetic"])
    workbook.save(path)

    report = import_rounds(path, repository)

    assert report.imported == 1
    saved = repository.list_rounds(origin="synthetic")
    assert saved[0].external_id == "excel-1"
    assert saved[0].multiplier == 3.5
    assert saved[0].played_at == datetime(2026, 10, 2, 9)


def test_import_counts_empty_rows(repository, tmp_path):
    path = tmp_path / "empty_rows.csv"
    path.write_text("multiplier\n1.5\n\n", encoding="utf-8")

    report = import_rounds(path, repository)

    assert report.imported == 1
    assert report.skipped_empty == 1
    assert not report.rejected


def test_import_reports_missing_multiplier_and_invalid_optional_date(repository, tmp_path):
    path = tmp_path / "missing.csv"
    path.write_text(
        "multiplier,played_at\n"
        ",2026-10-01T12:00:00\n"
        "2.25,pas-une-date\n"
        "3.50,\n",
        encoding="utf-8",
    )

    report = import_rounds(path, repository, default_origin="synthetic")

    assert report.imported == 1
    assert [issue.row_number for issue in report.rejected] == [2, 3]
    assert "multiplicateur" in report.rejected[0].message
    assert "date invalide" in report.rejected[1].message
    saved = repository.list_rounds()
    assert saved[0].origin == "synthetic"
    assert saved[0].played_at is None


def test_import_reports_rows_with_extra_columns(repository, tmp_path):
    path = tmp_path / "extra.csv"
    path.write_text("multiplier\n2.0,unexpected\n", encoding="utf-8")

    report = import_rounds(path, repository)

    assert report.imported == 0
    assert len(report.rejected) == 1
    assert "plus de champs" in report.rejected[0].message


def test_csv_bet_import_preserves_missing_amounts_without_payout_assumptions(
    repository, tmp_path
):
    path = tmp_path / "bets.csv"
    path.write_text(
        "external_id,stake_amount,cashout_multiplier,returned_amount,net_amount\n"
        "b-1,10,2,,\n"
        "b-2,10,2,25,\n",
        encoding="utf-8",
    )

    report = import_bets(path, repository)

    assert report.imported == 2
    first, second = repository.list_bets()
    assert first.returned_amount is None
    assert first.net_amount is None
    assert second.returned_amount == 25
    assert second.net_amount == 15


def test_excel_bet_import_reports_inconsistent_net(repository, tmp_path):
    path = tmp_path / "bets.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["stake_amount", "returned_amount", "net_amount"])
    sheet.append([5, 8, 1])
    sheet.append([5, 8, 3])
    workbook.save(path)

    report = import_bets(path, repository)

    assert report.imported == 1
    assert len(report.rejected) == 1
    assert report.rejected[0].row_number == 2
    assert "résultat net" in report.rejected[0].message


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("wrong.csv", "value\n2\n", "multiplier"),
        ("unsupported.json", "{}", "Format non pris en charge"),
        ("empty.csv", "", "en-tête"),
    ],
)
def test_import_rejects_unsupported_or_malformed_file(
    repository, tmp_path, filename, content, message
):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ImportFileError, match=message):
        import_rounds(path, repository)
