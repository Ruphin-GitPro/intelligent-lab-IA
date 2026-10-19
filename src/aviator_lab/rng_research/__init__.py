"""Diagnostics statistiques exploratoires sur les suites de manches."""

from .diagnostics import (
    AutocorrelationResult,
    LagResult,
    MultipleTestCorrection,
    PeriodComparisonResult,
    RunsTestResult,
    autocorrelation_diagnostic,
    compare_periods,
    correct_p_values,
    runs_test,
)

__all__ = [
    "AutocorrelationResult",
    "LagResult",
    "MultipleTestCorrection",
    "PeriodComparisonResult",
    "RunsTestResult",
    "autocorrelation_diagnostic",
    "compare_periods",
    "correct_p_values",
    "runs_test",
]
