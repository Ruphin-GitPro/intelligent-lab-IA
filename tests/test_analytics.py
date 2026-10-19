import pytest

from aviator_lab.analytics import descriptive_stats
from aviator_lab.probability import empirical_exceedance_probability
from aviator_lab.storage import RoundRepository


def test_descriptive_statistics_include_missing_values_and_distribution():
    result = descriptive_stats([1, 2, 2, 4, None], origin="real")

    assert result.total_rows == 5
    assert result.n_valid == 4
    assert result.n_missing == 1
    assert result.minimum == 1
    assert result.maximum == 4
    assert result.mean == 2.25
    assert result.median == 2
    assert result.quantiles[0.5] == 2
    assert result.threshold_counts[2.0] == 3
    assert result.threshold_frequencies[2.0] == 0.75
    assert sum(item.count for item in result.histogram) == result.n_valid
    assert result.longest_above_streak == 3
    assert result.longest_below_streak == 1


def test_missing_round_breaks_a_consecutive_sequence():
    result = descriptive_stats([2, None, 2], origin="real")

    assert result.longest_above_streak == 1


def test_descriptive_statistics_empty_and_singleton_samples():
    empty = descriptive_stats([None], origin="synthetic")
    singleton = descriptive_stats([1.25], origin="simulated")

    assert empty.n_valid == 0
    assert empty.n_missing == 1
    assert empty.mean is None
    assert empty.histogram
    assert singleton.sample_std_dev is None
    assert singleton.mean == 1.25


@pytest.mark.parametrize("bad_values", [[0], [float("inf")], [True], ["bad"]])
def test_descriptive_statistics_reject_invalid_values(bad_values):
    with pytest.raises(ValueError):
        descriptive_stats(bad_values, origin="real")


def test_descriptive_statistics_reject_invalid_origin():
    with pytest.raises(ValueError, match="origine"):
        descriptive_stats([2], origin="mixed")


def test_empirical_probability_uses_wilson_interval_and_reports_sample_size():
    result = empirical_exceedance_probability([1, 2, 2, 4, None], 2, origin="real")

    assert result.successes == 3
    assert result.n_valid == 4
    assert result.n_missing == 1
    assert result.estimate == 0.75
    assert 0 < result.confidence_low < result.estimate
    assert result.estimate < result.confidence_high < 1
    assert result.confidence_level == 0.95


def test_empirical_probability_handles_all_successes_and_failures():
    all_success = empirical_exceedance_probability([2, 3], 2, origin="real")
    all_failure = empirical_exceedance_probability([1, 1.5], 2, origin="real")

    assert all_success.estimate == 1
    assert all_success.confidence_high == 1
    assert all_success.confidence_low > 0
    assert all_failure.estimate == 0
    assert all_failure.confidence_low == 0
    assert all_failure.confidence_high < 1


def test_empirical_probability_rejects_empty_sample_and_bad_parameters():
    with pytest.raises(ValueError, match="Aucune manche valide"):
        empirical_exceedance_probability([None], 2, origin="real")
    with pytest.raises(ValueError, match="seuil"):
        empirical_exceedance_probability([2], 0.5, origin="real")
    with pytest.raises(ValueError, match="confiance"):
        empirical_exceedance_probability([2], 2, origin="real", confidence_level=1)


def test_analyses_can_filter_real_and_simulated_rounds_separately(tmp_path):
    repository = RoundRepository(tmp_path / "origins.sqlite")
    repository.initialize()
    try:
        repository.add_round(1.5, source="manual", origin="real")
        repository.add_round(4.0, source="simulation", origin="simulated")
        real = repository.list_rounds(origin="real")
        simulated = repository.list_rounds(origin="simulated")

        real_stats = descriptive_stats([row.multiplier for row in real], origin="real")
        simulated_stats = descriptive_stats(
            [row.multiplier for row in simulated], origin="simulated"
        )
        assert real_stats.mean == 1.5
        assert simulated_stats.mean == 4.0
        assert real_stats.origin != simulated_stats.origin
    finally:
        repository.close()
