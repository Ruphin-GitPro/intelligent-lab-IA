"""Diagnostics exploratoires de dépendance et de différences temporelles.

Ces tests examinent les hypothèses statistiques spécifiées; un petit p-value ne
prouve ni une faille du générateur, ni un avantage prédictif ou financier.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Iterable

from scipy.stats import mannwhitneyu, norm
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.stats.multitest import multipletests

from aviator_lab.analytics.descriptive import VALID_ORIGINS, _validated_values


@dataclass(frozen=True)
class LagResult:
    """Résultat du test de Ljung–Box jusqu'au retard demandé."""

    lag: int
    statistic: float
    p_value: float
    p_value_holm: float


@dataclass(frozen=True)
class AutocorrelationResult:
    origin: str
    n: int
    alpha: float
    lags: tuple[LagResult, ...]
    detected_after_correction: bool
    null_hypothesis: str
    limitation: str


@dataclass(frozen=True)
class RunsTestResult:
    origin: str
    n: int
    n_below: int
    n_above: int
    ties_excluded: int
    cutoff: float
    runs: int
    z_statistic: float
    p_value: float
    alpha: float
    detected: bool
    null_hypothesis: str
    limitation: str


@dataclass(frozen=True)
class PeriodComparisonResult:
    origin: str
    n_first: int
    n_second: int
    median_first: float
    median_second: float
    u_statistic: float
    p_value: float
    alpha: float
    detected: bool
    null_hypothesis: str
    limitation: str


@dataclass(frozen=True)
class AdjustedPValue:
    raw: float
    adjusted: float
    rejected: bool


@dataclass(frozen=True)
class MultipleTestCorrection:
    method: str
    alpha: float
    results: tuple[AdjustedPValue, ...]


def _values(values: Iterable[object], origin: str, *, minimum: int) -> list[float]:
    if origin not in VALID_ORIGINS:
        raise ValueError("L'origine doit être real, synthetic ou simulated.")
    result, missing = _validated_values(values)
    if missing:
        raise ValueError("Le test exige une séquence complète; corrigez les données manquantes.")
    if len(result) < minimum:
        raise ValueError(f"Au moins {minimum} observations valides sont requises.")
    return result


def _validate_alpha(alpha: float) -> float:
    alpha = float(alpha)
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha doit être compris entre 0 et 1.")
    return alpha


def autocorrelation_diagnostic(
    values: Iterable[object],
    *,
    origin: str,
    lags: int = 10,
    alpha: float = 0.05,
) -> AutocorrelationResult:
    """Applique Ljung–Box à tous les retards de 1 à ``lags``.

    H0 : les autocorrélations jusqu'au retard étudié sont nulles. Les p-values
    aux retards successifs sont ajustées avec Holm. Le test suppose une série
    correctement ordonnée et des observations régulièrement espacées; il ne
    teste pas toutes les formes possibles de dépendance.
    """
    alpha = _validate_alpha(alpha)
    if isinstance(lags, bool) or not isinstance(lags, int) or lags < 1:
        raise ValueError("lags doit être un entier strictement positif.")
    sample = _values(values, origin, minimum=max(8, lags + 1))
    frame = acorr_ljungbox(sample, lags=list(range(1, lags + 1)), return_df=True)
    raw_p = [float(value) for value in frame["lb_pvalue"]]
    adjusted_p = multipletests(raw_p, alpha=alpha, method="holm")[1]
    results = tuple(
        LagResult(
            lag=lag,
            statistic=float(frame.loc[lag, "lb_stat"]),
            p_value=raw_p[lag - 1],
            p_value_holm=float(adjusted_p[lag - 1]),
        )
        for lag in range(1, lags + 1)
    )
    return AutocorrelationResult(
        origin=origin,
        n=len(sample),
        alpha=alpha,
        lags=results,
        detected_after_correction=any(item.p_value_holm < alpha for item in results),
        null_hypothesis="Les autocorrélations aux retards testés sont nulles.",
        limitation=(
            "L'ordre doit refléter l'ordre réel des manches; une détection ne prouve "
            "ni prédictibilité, ni avantage financier."
        ),
    )


def runs_test(
    values: Iterable[object],
    *,
    origin: str,
    cutoff: float | None = None,
    alpha: float = 0.05,
) -> RunsTestResult:
    """Test normal approximatif des séquences au-dessus/au-dessous d'un seuil.

    Les observations égales au seuil sont écartées. H0 : l'ordre des deux
    catégories est aléatoire. Le seuil médian est calculé sur l'échantillon si
    ``cutoff`` n'est pas fourni. L'approximation demande au moins 10 valeurs
    conservées et au moins deux de chaque catégorie.
    """
    alpha = _validate_alpha(alpha)
    sample = _values(values, origin, minimum=10)
    cutoff = median(sample) if cutoff is None else float(cutoff)
    if not math.isfinite(cutoff) or cutoff < 1:
        raise ValueError("Le seuil doit être fini et supérieur ou égal à 1.")
    labels = [1 if value > cutoff else 0 for value in sample if value != cutoff]
    ties = len(sample) - len(labels)
    n_below = labels.count(0)
    n_above = labels.count(1)
    n = len(labels)
    if n < 10 or min(n_below, n_above) < 2:
        raise ValueError("Le test exige au moins 10 valeurs hors seuil et 2 par catégorie.")
    runs = 1 + sum(left != right for left, right in zip(labels, labels[1:]))
    expected = 1 + (2 * n_below * n_above / n)
    variance = (
        2 * n_below * n_above * (2 * n_below * n_above - n)
        / (n * n * (n - 1))
    )
    if variance <= 0:
        raise ValueError("Variance nulle : test des séquences impossible.")
    z_statistic = (runs - expected) / math.sqrt(variance)
    p_value = float(2 * norm.sf(abs(z_statistic)))
    return RunsTestResult(
        origin=origin,
        n=n,
        n_below=n_below,
        n_above=n_above,
        ties_excluded=ties,
        cutoff=cutoff,
        runs=runs,
        z_statistic=z_statistic,
        p_value=p_value,
        alpha=alpha,
        detected=p_value < alpha,
        null_hypothesis="L'ordre des catégories au-dessus et au-dessous du seuil est aléatoire.",
        limitation=(
            "Test approximatif sensible au seuil et aux ex æquo; une détection "
            "n'identifie pas la cause ni ne prouve une capacité prédictive."
        ),
    )


def compare_periods(
    first_period: Iterable[object],
    second_period: Iterable[object],
    *,
    origin: str,
    alpha: float = 0.05,
) -> PeriodComparisonResult:
    """Compare les distributions de deux périodes par test de Mann–Whitney.

    H0 : les deux échantillons indépendants proviennent de distributions ayant
    la même position stochastique. Le test ne localise pas un changement dans le
    temps et ne démontre pas un effet causal ou une rentabilité.
    """
    alpha = _validate_alpha(alpha)
    first = _values(first_period, origin, minimum=2)
    second = _values(second_period, origin, minimum=2)
    result = mannwhitneyu(first, second, alternative="two-sided", method="auto")
    p_value = float(result.pvalue)
    return PeriodComparisonResult(
        origin=origin,
        n_first=len(first),
        n_second=len(second),
        median_first=float(median(first)),
        median_second=float(median(second)),
        u_statistic=float(result.statistic),
        p_value=p_value,
        alpha=alpha,
        detected=p_value < alpha,
        null_hypothesis="Les deux périodes ont la même position stochastique.",
        limitation=(
            "Suppose deux échantillons indépendants; dépendance temporelle, "
            "sélection des périodes et changements multiples peuvent invalider l'interprétation. "
            "Une différence n'établit aucune prédictibilité ni rentabilité."
        ),
    )


def correct_p_values(
    p_values: Iterable[float],
    *,
    method: str = "holm",
    alpha: float = 0.05,
) -> MultipleTestCorrection:
    """Ajuste une famille de p-values avec Holm ou Benjamini–Hochberg."""
    alpha = _validate_alpha(alpha)
    if method not in {"holm", "fdr_bh"}:
        raise ValueError("Méthode inconnue; utilisez 'holm' ou 'fdr_bh'.")
    raw = [float(value) for value in p_values]
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in raw):
        raise ValueError("Chaque p-value doit être un nombre fini entre 0 et 1.")
    if not raw:
        return MultipleTestCorrection(method, alpha, ())
    rejected, adjusted, _, _ = multipletests(raw, alpha=alpha, method=method)
    return MultipleTestCorrection(
        method=method,
        alpha=alpha,
        results=tuple(
            AdjustedPValue(raw_value, float(adjusted_value), bool(is_rejected))
            for raw_value, adjusted_value, is_rejected in zip(raw, adjusted, rejected)
        ),
    )
