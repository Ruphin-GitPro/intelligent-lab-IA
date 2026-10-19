from decimal import Decimal

import pytest

from aviator_lab.analytics import financial_summary
from aviator_lab.storage import DuplicateBetError, RoundRepository, ValidationError


@pytest.fixture
def repository(tmp_path):
    repo = RoundRepository(tmp_path / "financial.sqlite")
    repo.initialize()
    yield repo
    repo.close()


def test_bet_storage_keeps_return_distinct_and_calculates_net_arithmetically(repository):
    bet = repository.add_bet(
        source="relevé fourni",
        external_id="bet-1",
        stake_amount="10.00",
        cashout_multiplier="2.5",
        returned_amount="25.00",
    )

    assert bet.stake_amount == Decimal("10.0000")
    assert bet.returned_amount == Decimal("25.0000")
    assert bet.net_amount == Decimal("15.0000")
    assert bet.returned_amount != bet.net_amount


def test_financial_summary_calculates_totals_streak_and_drawdown(repository):
    for index, (returned, net) in enumerate(
        [("0", "-10"), ("25", "15"), ("0", "-10"), ("0", "-10")], start=1
    ):
        repository.add_bet(
            source="synthetic fixture",
            origin="synthetic",
            external_id=f"b-{index}",
            stake_amount="10",
            returned_amount=returned,
            net_amount=net,
        )

    summary = financial_summary(repository.list_bets(origin="synthetic"), origin="synthetic")

    assert summary.bet_count == 4
    assert summary.complete_bet_count == 4
    assert summary.total_staked == Decimal("40.0000")
    assert summary.total_returned == Decimal("25.0000")
    assert summary.net_result == Decimal("-15.0000")
    assert (summary.wins, summary.losses, summary.break_even) == (1, 3, 0)
    assert summary.longest_loss_streak == 2
    assert summary.max_drawdown == Decimal("20.0000")
    assert summary.drawdown_complete
    assert [balance for _, balance in summary.cumulative_net_curve] == [
        Decimal("-10.0000"), Decimal("5.0000"), Decimal("-5.0000"), Decimal("-15.0000")
    ]


def test_missing_return_is_not_invented_and_disables_full_drawdown(repository):
    repository.add_bet(
        source="partial file",
        stake_amount="5",
        cashout_multiplier="2",
    )

    bet = repository.list_bets()[0]
    summary = financial_summary([bet], origin="real")

    assert bet.returned_amount is None
    assert bet.net_amount is None
    assert summary.total_staked == Decimal("5.0000")
    assert summary.total_returned is None
    assert summary.net_result is None
    assert summary.incomplete_bet_count == 1
    assert summary.max_drawdown is None
    assert not summary.drawdown_complete
    assert not summary.cumulative_net_curve


def test_explicit_net_without_return_is_kept_as_provided(repository):
    repository.add_bet(source="user file", stake_amount=10, net_amount=-3)

    summary = financial_summary(repository.list_bets(), origin="real")

    assert summary.net_result == Decimal("-3.0000")
    assert summary.bets_with_net == 1
    assert summary.bets_with_return == 0
    assert summary.total_returned is None


def test_bet_validation_rejects_inconsistent_or_invalid_values(repository):
    with pytest.raises(ValidationError, match="égal"):
        repository.add_bet(
            source="manual", stake_amount=10, returned_amount=20, net_amount=5
        )
    with pytest.raises(ValidationError, match="fini"):
        repository.add_bet(source="manual", stake_amount=-1)
    with pytest.raises(ValidationError, match="multiplicateur"):
        repository.add_bet(source="manual", cashout_multiplier=0.5)
    with pytest.raises(ValidationError, match="Au moins"):
        repository.add_bet(source="manual")
    with pytest.raises(ValidationError, match="devise"):
        repository.add_bet(source="manual", stake_amount=1, currency="EURO")
    with pytest.raises(ValidationError, match="quatre décimales"):
        repository.add_bet(source="manual", stake_amount="1.00001")


def test_bet_identifiers_and_round_origins_are_checked(repository):
    repository.add_bet(source="manual", external_id="id-1", stake_amount=1)
    with pytest.raises(DuplicateBetError):
        repository.add_bet(source="manual", external_id="id-1", stake_amount=2)

    round_record = repository.add_round(2, source="sim", origin="simulated")
    with pytest.raises(ValidationError, match="même origine"):
        repository.add_bet(source="real file", round_id=round_record.id, stake_amount=1)


def test_financial_analysis_rejects_mixed_origins(repository):
    repository.add_bet(source="manual", stake_amount=1, origin="real")
    repository.add_bet(source="test", stake_amount=1, origin="synthetic")

    with pytest.raises(ValueError, match="mélanger"):
        financial_summary(repository.list_bets(), origin="real")


def test_financial_analysis_rejects_mixed_currencies(repository):
    repository.add_bet(source="file A", stake_amount=10, currency="MGA")
    repository.add_bet(source="file B", stake_amount=1, currency="EUR")

    with pytest.raises(ValueError, match="devises"):
        financial_summary(repository.list_bets(), origin="real")
