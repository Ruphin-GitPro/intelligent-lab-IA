"""Prévisions naïves à un pas et vérification chronologique hors échantillon."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class ForecastPoint:
    """Une prévision faite avant une manche, sans utiliser son résultat."""

    round_number: int
    history_count: int
    prediction: float
    interval_low: float | None
    interval_high: float | None
    actual: float
    signed_error: float
    absolute_error: float
    last_value_baseline: float
    baseline_absolute_error: float


def _clean_values(values: Iterable[object]) -> list[float]:
    clean: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Les multiplicateurs doivent être des nombres finis supérieurs ou égaux à 1.") from None
        if isinstance(value, bool) or not math.isfinite(number) or number < 1:
            raise ValueError("Les multiplicateurs doivent être des nombres finis supérieurs ou égaux à 1.")
        clean.append(number)
    return clean


def next_round_median(values: Iterable[object], *, window: int = 10) -> float | None:
    """Estimation centrale par médiane des dernières manches disponibles."""
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("La fenêtre doit être un entier strictement positif.")
    clean = _clean_values(values)
    return median(clean[-window:]) if clean else None


def next_round_interval(
    values: Iterable[object], *, window: int = 10, lower_quantile: float = 0.10,
    upper_quantile: float = 0.90,
) -> tuple[float, float] | None:
    """Fourchette descriptive des quantiles passés; ce n'est pas un IC ni une garantie."""
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("La fenêtre doit être un entier strictement positif.")
    if not (0 <= lower_quantile < upper_quantile <= 1):
        raise ValueError("Les quantiles doivent respecter 0 ≤ bas < haut ≤ 1.")
    clean = _clean_values(values)
    sample = clean[-window:]
    if not sample:
        return None
    if len(sample) < window:
        return None
    low, high = np.quantile(sample, [lower_quantile, upper_quantile])
    return float(low), float(high)


def rolling_median_backtest(
    values: Iterable[object], *, window: int = 10, limit: int = 20
) -> tuple[ForecastPoint, ...]:
    """Évalue les dernières manches en ordre temporel, avec les seules données antérieures.

    La référence naïve prédit que la prochaine valeur sera égale au dernier
    multiplicateur déjà observé. Les écarts absolus permettent de comparer les
    deux méthodes avec la même métrique.
    """
    for value, label in ((window, "La fenêtre"), (limit, "La limite")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{label} doit être un entier strictement positif.")
    clean = _clean_values(values)
    first_index = max(1, len(clean) - limit)
    points: list[ForecastPoint] = []
    for index in range(first_index, len(clean)):
        history = clean[max(0, index - window):index]
        predicted = median(history)
        interval = (
            np.quantile(history, [0.10, 0.90]) if len(history) >= window else None
        )
        actual = clean[index]
        baseline = history[-1]
        points.append(ForecastPoint(
            round_number=index + 1,
            history_count=len(history),
            prediction=predicted,
            interval_low=float(interval[0]) if interval is not None else None,
            interval_high=float(interval[1]) if interval is not None else None,
            actual=actual,
            signed_error=actual - predicted,
            absolute_error=abs(actual - predicted),
            last_value_baseline=baseline,
            baseline_absolute_error=abs(actual - baseline),
        ))
    return tuple(points)
