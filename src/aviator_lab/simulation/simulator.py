"""Simulations bootstrap de stratégies à argent fictif."""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import stdev
from typing import Iterable

import numpy as np

from aviator_lab.analytics.descriptive import VALID_ORIGINS, _validated_values


@dataclass(frozen=True)
class TrialOutcome:
    """Résumé d'une trajectoire simulée."""

    net_result: float
    final_bankroll: float
    max_drawdown: float
    rounds_played: int
    ruin: bool
    stopped_by_loss_limit: bool
    max_loss_streak: int


@dataclass(frozen=True)
class SimulationResult:
    """Résultats simulés, conservés distinctement des données de référence."""

    origin: str
    reference_origin: str
    reference_rows: int
    reference_valid: int
    reference_missing: int
    stake: float
    cashout_multiplier: float
    starting_bankroll: float
    max_rounds: int
    loss_limit: float | None
    trials: int
    seed: int
    mean_net_result: float
    median_net_result: float
    percentile_05_net: float
    percentile_95_net: float
    mean_volatility: float | None
    mean_max_drawdown: float
    percentile_95_max_drawdown: float
    ruin_probability: float
    loss_limit_stop_probability: float
    mean_rounds_played: float
    outcomes: tuple[TrialOutcome, ...]
    payout_assumption: str
    independence_assumption: str


def bootstrap_simulation(
    observed_multipliers: Iterable[object],
    *,
    reference_origin: str,
    stake: float,
    cashout_multiplier: float,
    starting_bankroll: float,
    max_rounds: int,
    trials: int = 1000,
    loss_limit: float | None = None,
    seed: int = 42,
) -> SimulationResult:
    """Simule une stratégie fixe en rééchantillonnant les manches observées.

    Chaque manche de chaque essai est tirée avec remise dans l'historique fourni.
    Si l'observation atteint le retrait, le retour brut hypothétique est
    ``mise * multiplicateur_de_retrait``; sinon le retour est nul. Cette règle
    est une hypothèse de simulation à vérifier, pas une confirmation des règles
    de paiement du jeu. Les résultats ne valent que sous l'hypothèse d'un
    rééchantillonnage IID à partir de l'historique, qui ignore les dépendances.
    """
    if reference_origin not in VALID_ORIGINS:
        raise ValueError("L'origine de référence doit être real, synthetic ou simulated.")
    values, missing = _validated_values(observed_multipliers)
    source_rows = len(values) + missing
    if not values:
        raise ValueError("Aucune observation valide pour construire le bootstrap.")

    stake = _positive_finite(stake, "La mise")
    cashout_multiplier = _positive_finite(cashout_multiplier, "Le multiplicateur de retrait")
    if cashout_multiplier < 1:
        raise ValueError("Le multiplicateur de retrait doit être supérieur ou égal à 1.")
    starting_bankroll = _positive_finite(starting_bankroll, "La bankroll initiale")
    if stake > starting_bankroll:
        raise ValueError("La mise ne peut pas dépasser la bankroll initiale.")
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        raise ValueError("La durée maximale doit être un entier strictement positif.")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("Le nombre de simulations doit être un entier strictement positif.")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("La graine aléatoire doit être un entier positif ou nul.")
    if loss_limit is not None:
        loss_limit = _positive_finite(loss_limit, "La limite de pertes")

    rng = np.random.default_rng(seed)
    outcomes: list[TrialOutcome] = []
    volatilities: list[float] = []
    for _ in range(trials):
        bankroll = starting_bankroll
        peak = bankroll
        max_drawdown = 0.0
        net_result = 0.0
        net_by_round: list[float] = []
        rounds_played = 0
        ruin = False
        stopped_by_limit = False
        current_loss_streak = max_loss_streak = 0

        for _round in range(max_rounds):
            if bankroll < stake:
                ruin = True
                break
            observed = values[int(rng.integers(0, len(values)))]
            if observed >= cashout_multiplier:
                round_net = stake * (cashout_multiplier - 1)
            else:
                round_net = -stake
            net_result += round_net
            bankroll += round_net
            rounds_played += 1
            net_by_round.append(round_net)
            peak = max(peak, bankroll)
            max_drawdown = max(max_drawdown, peak - bankroll)

            if round_net < 0:
                current_loss_streak += 1
                max_loss_streak = max(max_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0

            if bankroll <= 0:
                ruin = True
                break
            if loss_limit is not None and net_result <= -loss_limit:
                stopped_by_limit = True
                break

        if bankroll < stake:
            ruin = True

        outcomes.append(
            TrialOutcome(
                net_result=net_result,
                final_bankroll=bankroll,
                max_drawdown=max_drawdown,
                rounds_played=rounds_played,
                ruin=ruin,
                stopped_by_loss_limit=stopped_by_limit,
                max_loss_streak=max_loss_streak,
            )
        )
        if len(net_by_round) > 1:
            volatilities.append(stdev(net_by_round))

    net_values = np.array([item.net_result for item in outcomes], dtype=float)
    drawdowns = np.array([item.max_drawdown for item in outcomes], dtype=float)

    return SimulationResult(
        origin="simulated",
        reference_origin=reference_origin,
        reference_rows=source_rows,
        reference_valid=len(values),
        reference_missing=missing,
        stake=stake,
        cashout_multiplier=cashout_multiplier,
        starting_bankroll=starting_bankroll,
        max_rounds=max_rounds,
        loss_limit=loss_limit,
        trials=trials,
        seed=seed,
        mean_net_result=float(np.mean(net_values)),
        median_net_result=float(np.median(net_values)),
        percentile_05_net=float(np.quantile(net_values, 0.05)),
        percentile_95_net=float(np.quantile(net_values, 0.95)),
        mean_volatility=float(np.mean(volatilities)) if volatilities else None,
        mean_max_drawdown=float(np.mean(drawdowns)),
        percentile_95_max_drawdown=float(np.quantile(drawdowns, 0.95)),
        ruin_probability=float(np.mean([item.ruin for item in outcomes])),
        loss_limit_stop_probability=float(
            np.mean([item.stopped_by_loss_limit for item in outcomes])
        ),
        mean_rounds_played=float(np.mean([item.rounds_played for item in outcomes])),
        outcomes=tuple(outcomes),
        payout_assumption=(
            "Retour brut = mise × multiplicateur de retrait si M atteint ce seuil; "
            "sinon retour nul. Frais ignorés."
        ),
        independence_assumption=(
            "Bootstrap IID avec remise depuis l'historique fourni; les dépendances "
            "et changements de distribution ne sont pas modélisés."
        ),
    )


def _positive_finite(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} doit être un nombre fini positif.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} doit être un nombre fini positif.")
    return number
