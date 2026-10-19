import pytest

from aviator_lab.simulation import bootstrap_simulation


def test_bootstrap_simulation_is_reproducible_and_marked_simulated():
    first = bootstrap_simulation(
        [1.2, 3.0, 1.5, 2.5],
        reference_origin="synthetic",
        stake=10,
        cashout_multiplier=2,
        starting_bankroll=100,
        max_rounds=10,
        trials=25,
        seed=123,
    )
    second = bootstrap_simulation(
        [1.2, 3.0, 1.5, 2.5],
        reference_origin="synthetic",
        stake=10,
        cashout_multiplier=2,
        starting_bankroll=100,
        max_rounds=10,
        trials=25,
        seed=123,
    )

    assert first == second
    assert first.origin == "simulated"
    assert first.reference_origin == "synthetic"
    assert first.seed == 123
    assert first.trials == 25
    assert first.reference_valid == 4
    assert "Retour brut" in first.payout_assumption
    assert "IID" in first.independence_assumption


def test_bootstrap_simulation_reports_missing_source_values():
    result = bootstrap_simulation(
        [1.2, None, 2.5],
        reference_origin="real",
        stake=1,
        cashout_multiplier=2,
        starting_bankroll=10,
        max_rounds=2,
        trials=5,
    )

    assert result.reference_rows == 3
    assert result.reference_valid == 2
    assert result.reference_missing == 1


def test_loss_limit_stops_trials_and_ruin_is_measured():
    stopped = bootstrap_simulation(
        [1.01],
        reference_origin="synthetic",
        stake=10,
        cashout_multiplier=2,
        starting_bankroll=100,
        max_rounds=20,
        trials=4,
        loss_limit=20,
        seed=7,
    )
    ruined = bootstrap_simulation(
        [1.01],
        reference_origin="synthetic",
        stake=10,
        cashout_multiplier=2,
        starting_bankroll=20,
        max_rounds=20,
        trials=4,
        seed=7,
    )

    assert stopped.loss_limit_stop_probability == 1
    assert all(item.rounds_played == 2 for item in stopped.outcomes)
    assert stopped.ruin_probability == 0
    assert ruined.ruin_probability == 1
    assert all(item.final_bankroll == 0 for item in ruined.outcomes)


@pytest.mark.parametrize(
    "parameters",
    [
        {"stake": 0},
        {"cashout_multiplier": 0.5},
        {"starting_bankroll": 0},
        {"max_rounds": 0},
        {"trials": 0},
        {"seed": -1},
        {"loss_limit": 0},
    ],
)
def test_bootstrap_rejects_invalid_strategy_parameters(parameters):
    options = {
        "stake": 1,
        "cashout_multiplier": 2,
        "starting_bankroll": 10,
        "max_rounds": 2,
        "trials": 2,
        "seed": 42,
        "loss_limit": None,
    }
    options.update(parameters)
    with pytest.raises(ValueError):
        bootstrap_simulation([1.1, 3], reference_origin="real", **options)


def test_bootstrap_requires_valid_reference_sample_and_origin():
    common = {
        "stake": 1,
        "cashout_multiplier": 2,
        "starting_bankroll": 10,
        "max_rounds": 2,
    }
    with pytest.raises(ValueError, match="Aucune observation"):
        bootstrap_simulation([None], reference_origin="real", **common)
    with pytest.raises(ValueError, match="origine"):
        bootstrap_simulation([1.1], reference_origin="mixed", **common)
