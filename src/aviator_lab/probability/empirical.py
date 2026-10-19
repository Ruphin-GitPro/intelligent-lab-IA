"""Probabilités empiriques et intervalle de confiance de Wilson."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import NormalDist
from typing import Iterable

from aviator_lab.analytics.descriptive import VALID_ORIGINS, _validated_values


@dataclass(frozen=True)
class EmpiricalProbability:
    """Estimation de P(M >= threshold), avec son intervalle de Wilson."""

    origin: str
    threshold: float
    successes: int
    n_valid: int
    n_missing: int
    estimate: float
    confidence_level: float
    confidence_low: float
    confidence_high: float


def empirical_exceedance_probability(
    values: Iterable[object],
    threshold: float,
    *,
    origin: str,
    confidence_level: float = 0.95,
) -> EmpiricalProbability:
    """Estime P(M >= x) et l'IC Wilson bilatéral correspondant.

    L'intervalle de Wilson est adapté à une proportion binomiale, sous
    l'hypothèse que les manches observées sont des essais indépendants avec une
    probabilité stable. Dépendances, changements de période ou biais de collecte
    peuvent rendre cet intervalle trop optimiste. Il décrit l'incertitude de la
    fréquence dans ce cadre; il ne prédit pas le prochain résultat.
    """
    if origin not in VALID_ORIGINS:
        raise ValueError("L'origine doit être real, synthetic ou simulated.")
    threshold = float(threshold)
    if not math.isfinite(threshold) or threshold < 1:
        raise ValueError("Le seuil doit être fini et supérieur ou égal à 1.")
    confidence_level = float(confidence_level)
    if not 0 < confidence_level < 1:
        raise ValueError("Le niveau de confiance doit être compris entre 0 et 1.")

    clean, missing = _validated_values(values)
    n = len(clean)
    if n == 0:
        raise ValueError("Aucune manche valide : la probabilité empirique n'est pas estimable.")
    successes = sum(value >= threshold for value in clean)
    estimate = successes / n
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2)
    z_squared = z * z
    denominator = 1 + z_squared / n
    center = (estimate + z_squared / (2 * n)) / denominator
    margin = (
        z
        * math.sqrt(estimate * (1 - estimate) / n + z_squared / (4 * n * n))
        / denominator
    )
    return EmpiricalProbability(
        origin=origin,
        threshold=threshold,
        successes=successes,
        n_valid=n,
        n_missing=missing,
        estimate=estimate,
        confidence_level=confidence_level,
        confidence_low=max(0.0, center - margin),
        confidence_high=min(1.0, center + margin),
    )
