"""Assistant de retrait : objectifs de retrait, espérance, validation chronologique, Monte-Carlo fictif.

Ce module décrit et simule ; il ne prédit jamais la prochaine manche et ne conseille jamais un objectif.

Cadre de calcul (identique pour tous les objectifs, sous réserve des règles réelles du jeu, non confirmées) :
* Mise ``s``, objectif de retrait ``x`` > 1.
* Retrait réussi (multiplicateur de la manche ≥ x) : gain net ``s·(x − 1)``.
* Échec : perte nette ``s``.
* Espérance par manche : ``E = s·(p·x − 1)`` où ``p`` est la probabilité de réussite.

Probabilité théorique : sous l'HYPOTHÈSE d'un modèle « crash » classique avec taux de retour ``RTP``
(``P(M ≥ x) = RTP / x`` pour x ≥ RTP), l'espérance vaut ``s·(RTP − 1)`` quel que soit x. Cette hypothèse n'est
confirmée par aucune source dans ce projet : le RTP est un paramètre saisi par l'utilisateur.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

import numpy as np

from aviator_lab.probability import empirical_exceedance_probability

NO_ADVANTAGE = "AVANTAGE NON DÉMONTRÉ"
POSITIVE_GAP = "ÉCART POSITIF HORS ÉCHANTILLON — À CONFIRMER, AUCUNE GARANTIE"
DEFAULT_TARGETS = (1.5, 2.0, 3.0, 3.8, 5.0)
MIN_EVALUATION_ROUNDS = 200
MAX_SIMULATED_CELLS = 2_000_000

GENERAL_LIMITS = (
    "La fréquence historique décrit le passé; elle ne prédit pas la prochaine manche.",
    "L'intervalle de Wilson suppose des manches indépendantes et une probabilité stable.",
    "Les valeurs enregistrées proviennent de l'affichage de la page, sans validation de l'éditeur.",
    "Le barème de gain (mise × (x − 1)) est une hypothèse de calcul, pas une règle confirmée.",
)


def _positive_finite(value: object, label: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{label} doit être un nombre.") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} doit être un nombre fini strictement positif.")
    return number


def _target(value: object) -> float:
    number = _positive_finite(value, "L'objectif de retrait")
    if number <= 1:
        raise ValueError("L'objectif de retrait doit être strictement supérieur à 1.")
    return number


def _rtp(value: object) -> float | None:
    if value is None:
        return None
    number = _positive_finite(value, "Le RTP hypothétique")
    if number > 1:
        raise ValueError("Le RTP hypothétique doit être compris entre 0 et 1.")
    return number


def theoretical_probability(target: float, rtp: float | None) -> float | None:
    """P(M ≥ x) = min(1, RTP / x) sous l'hypothèse du modèle crash ; None si aucun RTP n'est posé."""
    if rtp is None:
        return None
    return min(1.0, rtp / _target(target))


def expectation(probability: float, target: float, stake: float) -> float:
    """Espérance nette par manche : stake · (p · x − 1)."""
    return stake * (probability * target - 1.0)


@dataclass(frozen=True)
class TargetAnalysis:
    target: float
    n: int
    successes: int
    frequency: float
    ci_low: float
    ci_high: float
    confidence_level: float
    theoretical_probability: float | None
    net_gain_on_success: float
    loss_on_failure: float
    theoretical_expectation: float | None
    empirical_expectation: float
    empirical_expectation_low: float
    empirical_expectation_high: float
    limits: tuple[str, ...]


def _limits(n: int, rtp: float | None) -> tuple[str, ...]:
    items = list(GENERAL_LIMITS)
    if n < MIN_EVALUATION_ROUNDS:
        items.append(f"Échantillon réduit (n={n}) : l'intervalle est large et la fréquence peu informative.")
    if rtp is None:
        items.append("Aucun RTP hypothétique saisi : la probabilité théorique n'est pas calculée.")
    else:
        items.append(f"Probabilité théorique calculée sous l'hypothèse RTP = {rtp:.2%}, non documentée par Bet261.")
    return tuple(items)


def analyze_target(
    values: Iterable[object],
    target: float,
    *,
    stake: float,
    rtp: float | None,
    origin: str,
    confidence_level: float = 0.95,
) -> TargetAnalysis:
    """Fréquence historique, IC de Wilson, gains/pertes et espérances pour un objectif de retrait."""
    target = _target(target)
    stake = _positive_finite(stake, "La mise")
    rtp = _rtp(rtp)
    estimate = empirical_exceedance_probability(
        values, target, origin=origin, confidence_level=confidence_level
    )
    p_theory = theoretical_probability(target, rtp)
    return TargetAnalysis(
        target=target,
        n=estimate.n_valid,
        successes=estimate.successes,
        frequency=estimate.estimate,
        ci_low=estimate.confidence_low,
        ci_high=estimate.confidence_high,
        confidence_level=estimate.confidence_level,
        theoretical_probability=p_theory,
        net_gain_on_success=stake * (target - 1.0),
        loss_on_failure=stake,
        theoretical_expectation=None if p_theory is None else expectation(p_theory, target, stake),
        empirical_expectation=expectation(estimate.estimate, target, stake),
        empirical_expectation_low=expectation(estimate.confidence_low, target, stake),
        empirical_expectation_high=expectation(estimate.confidence_high, target, stake),
        limits=_limits(estimate.n_valid, rtp),
    )


def chronological_split(
    values: Sequence[object], study_fraction: float = 0.7
) -> tuple[list[object], list[object]]:
    """Sépare une série DÉJÀ ordonnée dans le temps : début = étude, fin = évaluation. Aucun mélange."""
    fraction = float(study_fraction)
    if not 0.1 <= fraction <= 0.9:
        raise ValueError("La part d'étude doit être comprise entre 10 % et 90 %.")
    items = list(values)
    if len(items) < 2:
        raise ValueError("Au moins deux manches sont nécessaires pour une séparation chronologique.")
    cut = min(len(items) - 1, max(1, int(len(items) * fraction)))
    return items[:cut], items[cut:]


def minimum_rounds_for_evaluation(
    *, study_fraction: float = 0.7,
    min_evaluation_rounds: int = MIN_EVALUATION_ROUNDS,
) -> int:
    """Minimum total n whose chronological holdout reaches the requested evaluation size."""
    fraction = float(study_fraction)
    if not 0.1 <= fraction <= 0.9:
        raise ValueError("La part d'étude doit être comprise entre 10 % et 90 %.")
    if (
        isinstance(min_evaluation_rounds, bool)
        or not isinstance(min_evaluation_rounds, int)
        or min_evaluation_rounds < 1
    ):
        raise ValueError("Le minimum d'évaluation doit être un entier positif.")
    total = min_evaluation_rounds
    while total - int(total * fraction) < min_evaluation_rounds:
        total += 1
    return total


@dataclass(frozen=True)
class ComparisonRow:
    target: float
    study_n: int
    study_frequency: float
    study_expectation: float
    evaluation_n: int
    evaluation_frequency: float
    evaluation_ci_low: float
    evaluation_ci_high: float
    evaluation_expectation: float
    evaluation_expectation_low: float
    evaluation_expectation_high: float
    evaluation_net_variance: float
    theoretical_expectation: float | None


@dataclass(frozen=True)
class OutOfSampleComparison:
    rows: tuple[ComparisonRow, ...]
    selected_target: float
    verdict: str
    explanation: str
    study_n: int
    evaluation_n: int
    stake: float
    confidence_level: float
    familywise_confidence_level: float
    comparison_count: int
    no_bet_reference_expectation: float
    fee_per_round: float | None


def compare_targets_out_of_sample(
    values_in_chronological_order: Sequence[object],
    targets: Sequence[float],
    *,
    stake: float,
    rtp: float | None,
    origin: str,
    study_fraction: float = 0.7,
    confidence_level: float = 0.95,
    min_evaluation_rounds: int = MIN_EVALUATION_ROUNDS,
    fee_per_round: float | None = None,
) -> OutOfSampleComparison:
    """Choisit l'objectif sur la période d'étude, puis l'évalue sur la période suivante, sans jamais la réutiliser.

    Les seuils sont choisis sur la période d'étude et évalués sur la période ultérieure.
    Les intervalles de l'évaluation utilisent une correction de Bonferroni sur la famille
    prédéfinie. Le benchmark « ne pas miser » a une espérance nulle. Un verdict positif
    exige des frais documentés, au moins ``min_evaluation_rounds`` observations, et une
    borne basse après frais supérieure à ce benchmark.
    """
    clean_targets = sorted({_target(t) for t in targets})
    if not clean_targets:
        raise ValueError("Au moins un objectif de retrait est nécessaire.")
    stake = _positive_finite(stake, "La mise")
    rtp = _rtp(rtp)
    if fee_per_round is not None:
        if isinstance(fee_per_round, bool):
            raise ValueError("Les frais par manche doivent être un nombre fini positif ou nul.")
        try:
            fee_per_round = float(fee_per_round)
        except (TypeError, ValueError):
            raise ValueError("Les frais par manche doivent être un nombre fini positif ou nul.") from None
        if not math.isfinite(fee_per_round) or fee_per_round < 0:
            raise ValueError("Les frais par manche doivent être un nombre fini positif ou nul.")
    study, evaluation = chronological_split(values_in_chronological_order, study_fraction)
    familywise_confidence = 1.0 - (1.0 - confidence_level) / len(clean_targets)
    rows: list[ComparisonRow] = []
    for target in clean_targets:
        on_study = analyze_target(study, target, stake=stake, rtp=rtp, origin=origin, confidence_level=confidence_level)
        on_eval = analyze_target(
            evaluation, target, stake=stake, rtp=rtp, origin=origin,
            confidence_level=familywise_confidence,
        )
        fee = fee_per_round or 0.0
        expected = on_eval.empirical_expectation - fee
        expected_low = on_eval.empirical_expectation_low - fee
        expected_high = on_eval.empirical_expectation_high - fee
        net_variance = (
            stake**2 * target**2 * on_eval.frequency * (1.0 - on_eval.frequency)
            * on_eval.n / (on_eval.n - 1)
            if on_eval.n > 1 else 0.0
        )
        rows.append(ComparisonRow(
            target=target,
            study_n=on_study.n,
            study_frequency=on_study.frequency,
            study_expectation=on_study.empirical_expectation,
            evaluation_n=on_eval.n,
            evaluation_frequency=on_eval.frequency,
            evaluation_ci_low=on_eval.ci_low,
            evaluation_ci_high=on_eval.ci_high,
            evaluation_expectation=expected,
            evaluation_expectation_low=expected_low,
            evaluation_expectation_high=expected_high,
            evaluation_net_variance=net_variance,
            theoretical_expectation=on_eval.theoretical_expectation,
        ))
    chosen = max(rows, key=lambda row: (row.study_expectation, -row.target))
    base = (
        f"L'objectif {chosen.target:g}x a la meilleure espérance sur la période d'étude "
        f"({chosen.study_n} manches), mais ce choix est fait sur le passé."
    )
    if chosen.evaluation_n < min_evaluation_rounds:
        verdict = NO_ADVANTAGE
        explanation = (
            f"{base} La période d'évaluation ne compte que {chosen.evaluation_n} manches "
            f"(minimum retenu : {min_evaluation_rounds}) : aucun avantage ne peut être démontré."
        )
    elif fee_per_round is None:
        verdict = NO_ADVANTAGE
        explanation = (
            f"{base} Le résultat hors échantillon est comparé à la référence « ne pas miser » "
            "(espérance 0), avec des intervalles simultanés de Bonferroni. Les frais réels ne sont pas documentés; "
            "l'estimation affichée est brute, avant frais inconnus. Aucun avantage net ni rentabilité ne peut être affirmé."
        )
    elif chosen.evaluation_expectation_low > 0:
        verdict = POSITIVE_GAP
        explanation = (
            f"{base} Comparé à « ne pas miser » (espérance 0), sur {chosen.evaluation_n} manches suivantes, "
            f"la borne basse après frais documentés ({chosen.evaluation_expectation_low:+.3f} par manche) est positive. "
            "Cela peut être un hasard, "
            "un biais de collecte ou une période particulière : il faut le reproduire sur de nouvelles données, "
            "et rien n'indique que la prochaine manche sera favorable."
        )
    else:
        verdict = NO_ADVANTAGE
        explanation = (
            f"{base} Comparé à « ne pas miser » (espérance 0), sur {chosen.evaluation_n} manches suivantes, "
            "l'espérance estimée après frais documentés est "
            f"{chosen.evaluation_expectation:+.3f} par manche avec une borne basse de "
            f"{chosen.evaluation_expectation_low:+.3f} : aucun avantage robuste n'est démontré hors échantillon."
        )
    return OutOfSampleComparison(
        rows=tuple(rows), selected_target=chosen.target, verdict=verdict, explanation=explanation,
        study_n=chosen.study_n, evaluation_n=chosen.evaluation_n, stake=stake, confidence_level=confidence_level,
        familywise_confidence_level=familywise_confidence, comparison_count=len(clean_targets),
        no_bet_reference_expectation=0.0, fee_per_round=fee_per_round,
    )


@dataclass(frozen=True)
class FixedTargetSimulation:
    """Résultats Monte-Carlo en argent FICTIF. Ne constitue ni un résultat réel ni une prévision."""

    model: str
    success_probability: float
    stake: float
    target: float
    starting_bankroll: float
    rounds: int
    trials: int
    seed: int
    mean_final_bankroll: float
    median_final_bankroll: float
    final_bankroll_p05: float
    final_bankroll_p95: float
    mean_success_rate: float
    mean_cumulative_losses: float
    mean_max_drawdown: float
    p95_max_drawdown: float
    ruin_probability: float
    probability_final_above_start: float
    final_bankrolls: tuple[float, ...]
    assumption: str


def simulate_fixed_target(
    *,
    success_probability: float,
    stake: float,
    target: float,
    starting_bankroll: float,
    rounds: int,
    trials: int,
    seed: int = 42,
    model: str,
) -> FixedTargetSimulation:
    """Monte-Carlo d'une mise fixe avec objectif fixe, chaque manche étant un essai de Bernoulli indépendant.

    ``success_probability`` provient soit de la fréquence historique (modèle « historique »), soit du RTP
    hypothétique (modèle « théorique »). Le joueur s'arrête (ruine) dès que le solde est inférieur à la mise.
    """
    p = float(success_probability)
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("La probabilité de réussite doit être comprise entre 0 et 1.")
    stake = _positive_finite(stake, "La mise")
    target = _target(target)
    bankroll0 = _positive_finite(starting_bankroll, "Le montant initial")
    if stake > bankroll0:
        raise ValueError("La mise ne peut pas dépasser le montant initial.")
    for value, label in ((rounds, "Le nombre de manches"), (trials, "Le nombre de simulations")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{label} doit être un entier strictement positif.")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("La graine aléatoire doit être un entier positif ou nul.")
    if rounds * trials > MAX_SIMULATED_CELLS:
        raise ValueError(
            f"Simulation trop lourde (manches × simulations > {MAX_SIMULATED_CELLS:,}) : réduisez l'un des deux."
        )

    rng = np.random.default_rng(seed)
    wins = rng.random((trials, rounds)) < p
    delta = np.where(wins, stake * (target - 1.0), -stake)
    path = bankroll0 + np.cumsum(delta, axis=1)
    below = path < stake  # solde insuffisant pour la mise suivante
    ruined = below.any(axis=1)
    first = np.where(ruined, below.argmax(axis=1), rounds - 1)
    index = np.arange(rounds)[None, :]
    played = index <= first[:, None]
    frozen = np.where(played, path, np.take_along_axis(path, first[:, None], axis=1))
    final = frozen[:, -1]
    full = np.concatenate([np.full((trials, 1), bankroll0), frozen], axis=1)
    drawdown = (np.maximum.accumulate(full, axis=1) - full).max(axis=1)
    rounds_played = first + 1
    win_count = (wins & played).sum(axis=1)
    losses = (played & ~wins).sum(axis=1) * stake
    return FixedTargetSimulation(
        model=model,
        success_probability=p,
        stake=stake,
        target=target,
        starting_bankroll=bankroll0,
        rounds=rounds,
        trials=trials,
        seed=seed,
        mean_final_bankroll=float(final.mean()),
        median_final_bankroll=float(np.median(final)),
        final_bankroll_p05=float(np.percentile(final, 5)),
        final_bankroll_p95=float(np.percentile(final, 95)),
        mean_success_rate=float((win_count / rounds_played).mean()),
        mean_cumulative_losses=float(losses.mean()),
        mean_max_drawdown=float(drawdown.mean()),
        p95_max_drawdown=float(np.percentile(drawdown, 95)),
        ruin_probability=float(ruined.mean()),
        probability_final_above_start=float((final > bankroll0).mean()),
        final_bankrolls=tuple(float(v) for v in final),
        assumption=(
            "Manches indépendantes, probabilité de réussite constante, gain = mise × (x − 1), perte = mise, sans frais. "
            "Argent fictif : aucune garantie, aucun lien avec le prochain résultat réel."
        ),
    )
