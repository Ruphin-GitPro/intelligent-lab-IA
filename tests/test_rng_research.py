import pytest

from aviator_lab.rng_research import (
    autocorrelation_diagnostic,
    compare_periods,
    correct_p_values,
    runs_test,
)


def test_ljung_box_detects_strong_alternating_autocorrelation():
    result = autocorrelation_diagnostic([1, 2] * 40, origin="synthetic", lags=5)

    assert result.n == 80
    assert len(result.lags) == 5
    assert result.detected_after_correction
    assert all(item.p_value_holm >= item.p_value for item in result.lags)
    assert "ne prouve" in result.limitation


def test_ljung_box_rejects_missing_or_too_short_sequence():
    with pytest.raises(ValueError, match="manquantes"):
        autocorrelation_diagnostic([1, 2, None, 4, 5, 6, 7, 8], origin="real", lags=2)
    with pytest.raises(ValueError, match="observations"):
        autocorrelation_diagnostic([1, 2, 3], origin="real", lags=3)


def test_runs_test_flags_grouped_order_and_excludes_cutoff_ties():
    result = runs_test([1] * 30 + [1.5] * 5 + [2] * 30, origin="synthetic", cutoff=1.5)

    assert result.n_below == 30
    assert result.n_above == 30
    assert result.ties_excluded == 5
    assert result.runs == 2
    assert result.detected


def test_runs_test_requires_sufficient_values_in_each_class():
    with pytest.raises(ValueError, match="2 par catégorie"):
        runs_test([1] * 10 + [2], origin="real", cutoff=1.5)


def test_period_comparison_reports_sample_sizes_and_shift():
    result = compare_periods([1.1] * 20, [4.0] * 20, origin="real")

    assert result.n_first == result.n_second == 20
    assert result.median_first == 1.1
    assert result.median_second == 4.0
    assert result.detected
    assert "rentabilité" in result.limitation


def test_multiple_test_correction_holm_and_bh():
    holm = correct_p_values([0.01, 0.04, 0.2], method="holm")
    fdr = correct_p_values([0.01, 0.04, 0.2], method="fdr_bh")

    assert [item.adjusted for item in holm.results] == pytest.approx([0.03, 0.08, 0.2])
    assert holm.results[0].rejected
    assert fdr.method == "fdr_bh"
    assert len(fdr.results) == 3


@pytest.mark.parametrize(
    ("p_values", "method"),
    [([float("nan")], "holm"), ([1.1], "holm"), ([0.5], "bonferroni")],
)
def test_multiple_test_correction_rejects_invalid_inputs(p_values, method):
    with pytest.raises(ValueError):
        correct_p_values(p_values, method=method)
