"""Tests de l'assistant de retrait. Tous les jeux de données sont SYNTHÉTIQUES (générés ici)."""

import numpy as np
import pytest

from aviator_lab.analytics.withdrawal import (
    NO_ADVANTAGE,
    POSITIVE_GAP,
    analyze_target,
    chronological_split,
    compare_targets_out_of_sample,
    expectation,
    minimum_rounds_for_evaluation,
    simulate_fixed_target,
    theoretical_probability,
)


def synthetic_crash(n, rtp=0.97, seed=1):
    """Série SYNTHÉTIQUE suivant P(M >= x) = rtp / x, jamais issue de Bet261."""
    u = np.random.default_rng(seed).random(n)
    return [round(float(max(1.0, rtp / max(x, 1e-12))), 2) for x in u]


def test_gain_loss_and_expectation_formulas():
    result = analyze_target([1.5, 2.5, 3.0, 1.2], 2.0, stake=10, rtp=None, origin="synthetic")
    assert (result.n, result.successes, result.frequency) == (4, 2, 0.5)
    assert result.net_gain_on_success == 10.0 and result.loss_on_failure == 10.0
    assert result.empirical_expectation == pytest.approx(0.0)
    assert result.theoretical_probability is None and result.theoretical_expectation is None
    assert any("Aucun RTP" in item for item in result.limits)
    assert result.ci_low < result.frequency < result.ci_high


def test_theoretical_expectation_is_the_same_for_every_target_under_the_rtp_hypothesis():
    for target in (1.5, 2.0, 3.8, 5.0):
        p = theoretical_probability(target, 0.97)
        assert p == pytest.approx(0.97 / target)
        assert expectation(p, target, 10) == pytest.approx(-0.3)
    assert theoretical_probability(1.5, None) is None


@pytest.mark.parametrize("bad", [1.0, 0.5, 0, -2, float("nan"), float("inf"), "abc", None])
def test_invalid_targets_are_refused(bad):
    with pytest.raises(ValueError):
        analyze_target([2.0] * 5, bad, stake=1, rtp=None, origin="synthetic")


def test_invalid_stake_and_rtp_are_refused():
    with pytest.raises(ValueError):
        analyze_target([2.0] * 5, 2.0, stake=0, rtp=None, origin="synthetic")
    with pytest.raises(ValueError):
        analyze_target([2.0] * 5, 2.0, stake=1, rtp=1.5, origin="synthetic")
    with pytest.raises(ValueError):
        analyze_target([], 2.0, stake=1, rtp=None, origin="synthetic")


def test_chronological_split_keeps_order_without_shuffling():
    study, evaluation = chronological_split(list(range(10)), 0.7)
    assert study == [0, 1, 2, 3, 4, 5, 6] and evaluation == [7, 8, 9]
    with pytest.raises(ValueError):
        chronological_split([1], 0.7)
    with pytest.raises(ValueError):
        chronological_split(list(range(10)), 0.95)


def test_default_study_split_requires_664_rounds_for_200_evaluation_rounds():
    assert minimum_rounds_for_evaluation(study_fraction=0.7, min_evaluation_rounds=200) == 664


def test_iid_negative_expectation_data_never_shows_an_advantage():
    values = synthetic_crash(6000, rtp=0.97, seed=3)
    result = compare_targets_out_of_sample(
        values, [1.5, 2.0, 3.0, 3.8, 5.0], stake=1, rtp=0.97, origin="synthetic"
    )
    assert result.verdict == NO_ADVANTAGE
    assert result.study_n + result.evaluation_n == 6000
    assert result.study_n == 4200


def test_advantage_not_shown_when_evaluation_sample_is_too_small():
    result = compare_targets_out_of_sample([5.0] * 100, [2.0], stake=1, rtp=None, origin="synthetic")
    assert result.verdict == NO_ADVANTAGE
    assert "minimum retenu" in result.explanation


def test_current_110_rounds_leave_only_33_out_of_sample_rounds():
    result = compare_targets_out_of_sample(
        [1.8] * 110, [1.5, 2.0, 3.0, 3.8, 5.0],
        stake=1, rtp=None, origin="real", fee_per_round=None,
    )
    assert result.study_n == 77
    assert result.evaluation_n == 33
    assert result.verdict == NO_ADVANTAGE
    assert "minimum retenu : 200" in result.explanation


def test_positive_gap_is_labelled_unconfirmed_when_the_lower_bound_is_positive():
    # Série SYNTHÉTIQUE artificiellement favorable (tous les tours atteignent 2x) : ne reflète aucun jeu réel.
    result = compare_targets_out_of_sample(
        [3.0] * 1000, [2.0, 5.0], stake=1, rtp=None, origin="synthetic", fee_per_round=0,
    )
    assert result.selected_target == 2.0
    assert result.verdict == POSITIVE_GAP
    assert "AUCUNE GARANTIE" in result.verdict
    assert result.no_bet_reference_expectation == 0
    assert result.comparison_count == 2
    assert result.familywise_confidence_level == pytest.approx(0.975)
    assert result.rows[0].evaluation_net_variance == pytest.approx(0.0)


def test_missing_or_material_fees_prevent_a_positive_strategy_verdict():
    values = [3.0] * 1000  # Série synthétique volontairement favorable.
    undocumented = compare_targets_out_of_sample(
        values, [2.0], stake=1, rtp=None, origin="synthetic", fee_per_round=None,
    )
    assert undocumented.verdict == NO_ADVANTAGE
    assert "frais réels ne sont pas documentés" in undocumented.explanation

    fee_exceeds_gross_return = compare_targets_out_of_sample(
        values, [2.0], stake=1, rtp=None, origin="synthetic", fee_per_round=2.0,
    )
    assert fee_exceeds_gross_return.verdict == NO_ADVANTAGE
    assert fee_exceeds_gross_return.rows[0].evaluation_expectation < 0


def test_selection_uses_only_the_study_period():
    # Le meilleur objectif de la période d'étude (2x) échoue sur l'évaluation : verdict non démontré.
    values = [3.0] * 700 + [1.1] * 300
    result = compare_targets_out_of_sample(values, [2.0, 5.0], stake=1, rtp=None, origin="synthetic")
    assert result.selected_target == 2.0
    assert result.verdict == NO_ADVANTAGE


def test_simulation_is_deterministic_for_extreme_probabilities():
    sure = simulate_fixed_target(success_probability=1.0, stake=2, target=2.0, starting_bankroll=100,
                                 rounds=10, trials=5, model="test")
    assert sure.mean_final_bankroll == pytest.approx(120.0)
    assert sure.ruin_probability == 0 and sure.mean_cumulative_losses == 0
    assert sure.mean_success_rate == 1.0 and sure.mean_max_drawdown == 0

    never = simulate_fixed_target(success_probability=0.0, stake=10, target=2.0, starting_bankroll=35,
                                  rounds=10, trials=5, model="test")
    assert never.ruin_probability == 1.0
    assert never.mean_final_bankroll == pytest.approx(5.0)   # 35 - 3 mises, puis arrêt
    assert never.mean_cumulative_losses == pytest.approx(30.0)
    assert never.mean_max_drawdown == pytest.approx(30.0)
    assert never.mean_success_rate == 0.0


def test_simulation_is_reproducible_and_close_to_the_expectation():
    kwargs = dict(success_probability=0.485, stake=1, target=2.0, starting_bankroll=10_000,
                  rounds=200, trials=2000, seed=7, model="test")
    first, second = simulate_fixed_target(**kwargs), simulate_fixed_target(**kwargs)
    assert first.final_bankrolls == second.final_bankrolls
    expected = 10_000 + 200 * expectation(0.485, 2.0, 1)
    assert first.mean_final_bankroll == pytest.approx(expected, abs=3.0)
    assert 0.0 <= first.probability_final_above_start <= 1.0
    assert first.p95_max_drawdown >= first.mean_max_drawdown * 0.5


@pytest.mark.parametrize("kwargs", [
    dict(success_probability=1.2),
    dict(stake=0),
    dict(target=1.0),
    dict(starting_bankroll=0.5, stake=1),
    dict(rounds=0),
    dict(trials=-3),
    dict(seed=-1),
    dict(rounds=5000, trials=5000),
])
def test_simulation_refuses_invalid_or_too_heavy_inputs(kwargs):
    base = dict(success_probability=0.5, stake=1, target=2.0, starting_bankroll=100,
                rounds=10, trials=10, seed=1, model="test")
    base.update(kwargs)
    with pytest.raises(ValueError):
        simulate_fixed_target(**base)
