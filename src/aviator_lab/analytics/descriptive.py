"""Statistiques descriptives sur un échantillon d'une seule origine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, median, stdev
from typing import Iterable

import numpy as np


VALID_ORIGINS = {"real", "synthetic", "simulated"}
DEFAULT_BIN_EDGES = (1.0, 1.5, 2.0, 3.0, 5.0, 10.0, math.inf)


@dataclass(frozen=True)
class HistogramBin:
    """Effectif observé dans un intervalle [borne basse, borne haute)."""

    lower: float
    upper: float | None
    count: int


@dataclass(frozen=True)
class DescriptiveStats:
    """Résumé descriptif; n_valid identifie toujours la taille analysée."""

    origin: str
    total_rows: int
    n_valid: int
    n_missing: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None
    sample_std_dev: float | None
    quantiles: dict[float, float]
    histogram: tuple[HistogramBin, ...]
    threshold_counts: dict[float, int]
    threshold_frequencies: dict[float, float]
    streak_threshold: float
    longest_above_streak: int
    longest_below_streak: int


def _validated_values(values: Iterable[object]) -> tuple[list[float], int]:
    valid: list[float] = []
    missing = 0
    for value in values:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            missing += 1
            continue
        if isinstance(value, bool):
            raise ValueError("Un multiplicateur booléen n'est pas valide.")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Multiplicateur non numérique : {value!r}.") from exc
        if not math.isfinite(number) or number < 1:
            raise ValueError(f"Multiplicateur invalide : {value!r}; valeur finie >= 1 attendue.")
        valid.append(number)
    return valid, missing


def _validate_origin(origin: str) -> None:
    if origin not in VALID_ORIGINS:
        raise ValueError("L'origine doit être real, synthetic ou simulated.")


def _histogram(values: list[float], edges: tuple[float, ...]) -> tuple[HistogramBin, ...]:
    if len(edges) < 2 or edges[0] < 1 or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError("Les bornes doivent être strictement croissantes et commencer à 1 ou plus.")
    if not math.isinf(edges[-1]):
        raise ValueError("La dernière borne doit être math.inf pour inclure les valeurs extrêmes.")
    counts = [0] * (len(edges) - 1)
    for value in values:
        index = int(np.searchsorted(edges, value, side="right") - 1)
        if index >= len(counts):
            index = len(counts) - 1
        counts[index] += 1
    return tuple(
        HistogramBin(low, None if math.isinf(high) else high, count)
        for low, high, count in zip(edges, edges[1:], counts)
    )


def _longest_streak(values: list[float | None], threshold: float, above: bool) -> int:
    longest = current = 0
    for value in values:
        if value is None:
            current = 0
            continue
        matches = value >= threshold if above else value < threshold
        current = current + 1 if matches else 0
        longest = max(longest, current)
    return longest


def descriptive_stats(
    values: Iterable[object],
    *,
    origin: str,
    thresholds: Iterable[float] = (1.5, 2.0, 3.0, 5.0, 10.0),
    bin_edges: tuple[float, ...] = DEFAULT_BIN_EDGES,
    streak_threshold: float = 2.0,
) -> DescriptiveStats:
    """Calcule les indicateurs descriptifs, en comptant les valeurs manquantes.

    Les valeurs invalides provoquent une erreur au lieu d'être éliminées. `origin`
    est obligatoire afin que l'appelant indique explicitement le jeu analysé.
    Les quantiles utilisent l'interpolation linéaire de NumPy.
    """
    _validate_origin(origin)
    raw_values = list(values)
    clean, missing = _validated_values(raw_values)
    streak_values: list[float | None] = []
    for value in raw_values:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            streak_values.append(None)
        else:
            streak_values.append(float(value))
    clean_thresholds = tuple(float(value) for value in thresholds)
    if any(not math.isfinite(value) or value < 1 for value in clean_thresholds):
        raise ValueError("Chaque seuil doit être fini et supérieur ou égal à 1.")
    streak_threshold = float(streak_threshold)
    if not math.isfinite(streak_threshold) or streak_threshold < 1:
        raise ValueError("Le seuil de séquence doit être fini et supérieur ou égal à 1.")

    if clean:
        quantile_values = np.quantile(clean, [0.25, 0.5, 0.75, 0.9, 0.95])
        quantiles = dict(zip((0.25, 0.5, 0.75, 0.9, 0.95), map(float, quantile_values)))
        minimum, maximum = min(clean), max(clean)
        average, middle = mean(clean), median(clean)
        deviation = stdev(clean) if len(clean) > 1 else None
    else:
        quantiles = {}
        minimum = maximum = average = middle = deviation = None

    return DescriptiveStats(
        origin=origin,
        total_rows=len(clean) + missing,
        n_valid=len(clean),
        n_missing=missing,
        minimum=minimum,
        maximum=maximum,
        mean=average,
        median=middle,
        sample_std_dev=deviation,
        quantiles=quantiles,
        histogram=_histogram(clean, bin_edges),
        threshold_counts={
            threshold: sum(value >= threshold for value in clean)
            for threshold in clean_thresholds
        },
        threshold_frequencies={
            threshold: sum(value >= threshold for value in clean) / len(clean)
            for threshold in clean_thresholds
        } if clean else {threshold: 0.0 for threshold in clean_thresholds},
        streak_threshold=streak_threshold,
        longest_above_streak=_longest_streak(streak_values, streak_threshold, True),
        longest_below_streak=_longest_streak(streak_values, streak_threshold, False),
    )
