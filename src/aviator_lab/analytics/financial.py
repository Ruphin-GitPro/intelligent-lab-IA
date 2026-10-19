"""Agrégats de résultats financiers à partir des données personnelles fournies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from aviator_lab.storage import PersonalBet
from aviator_lab.analytics.descriptive import VALID_ORIGINS


ZERO = Decimal("0")


@dataclass(frozen=True)
class FinancialSummary:
    """Résumé d'un seul jeu d'origine; montants exprimés dans l'unité fournie."""

    origin: str
    currency: str | None
    bet_count: int
    complete_bet_count: int
    incomplete_bet_count: int
    bets_with_stake: int
    bets_with_return: int
    bets_with_net: int
    total_staked: Decimal | None
    total_returned: Decimal | None
    net_result: Decimal | None
    wins: int
    losses: int
    break_even: int
    average_net: Decimal | None
    net_sample_std_dev: Decimal | None
    longest_loss_streak: int
    max_drawdown: Decimal | None
    cumulative_net_curve: tuple[tuple[int, Decimal], ...]
    drawdown_complete: bool


def financial_summary(bets: Iterable[PersonalBet], *, origin: str) -> FinancialSummary:
    """Calcule les montants observés; n'estime jamais une mise ou un retour absent.

    Le résultat net n'est analysé que s'il est présent. `add_bet` le calcule par
    arithmétique simple (retour - mise) quand ces deux montants sont disponibles.
    Le drawdown et la courbe de résultat net cumulé ne sont fournis que si tous
    les paris de la sélection ont un net, dans l'ordre d'insertion de la base.
    """
    if origin not in VALID_ORIGINS:
        raise ValueError("L'origine doit être real, synthetic ou simulated.")
    ordered = sorted(bets, key=lambda bet: bet.id or 0)
    if any(bet.origin != origin for bet in ordered):
        raise ValueError("L'analyse financière ne peut pas mélanger les origines.")
    currencies = {bet.currency for bet in ordered}
    if len(currencies) > 1:
        raise ValueError("L'analyse financière ne peut pas additionner plusieurs devises.")

    stakes = [bet.stake_amount for bet in ordered if bet.stake_amount is not None]
    returned = [bet.returned_amount for bet in ordered if bet.returned_amount is not None]
    net_values = [bet.net_amount for bet in ordered if bet.net_amount is not None]
    wins = sum(value > 0 for value in net_values)
    losses = sum(value < 0 for value in net_values)
    break_even = sum(value == 0 for value in net_values)

    longest_loss = current_loss = 0
    for bet in ordered:
        if bet.net_amount is None:
            current_loss = 0
        elif bet.net_amount < 0:
            current_loss += 1
            longest_loss = max(longest_loss, current_loss)
        else:
            current_loss = 0

    all_net_known = bool(ordered) and len(net_values) == len(ordered)
    curve: list[tuple[int, Decimal]] = []
    max_drawdown: Decimal | None = None
    if all_net_known:
        balance = peak = ZERO
        max_drawdown = ZERO
        for bet in ordered:
            balance += bet.net_amount
            peak = max(peak, balance)
            max_drawdown = max(max_drawdown, peak - balance)
            curve.append((bet.id, balance))

    average_net = sum(net_values, ZERO) / Decimal(len(net_values)) if net_values else None
    net_sample_std_dev = None
    if len(net_values) > 1:
        squared_deviations = sum(((value - average_net) ** 2 for value in net_values), ZERO)
        net_sample_std_dev = (squared_deviations / Decimal(len(net_values) - 1)).sqrt()

    return FinancialSummary(
        origin=origin,
        currency=next(iter(currencies), None),
        bet_count=len(ordered),
        complete_bet_count=sum(
            bet.stake_amount is not None
            and bet.returned_amount is not None
            and bet.net_amount is not None
            for bet in ordered
        ),
        incomplete_bet_count=sum(
            bet.stake_amount is None
            or bet.returned_amount is None
            or bet.net_amount is None
            for bet in ordered
        ),
        bets_with_stake=len(stakes),
        bets_with_return=len(returned),
        bets_with_net=len(net_values),
        total_staked=sum(stakes, ZERO) if stakes else None,
        total_returned=sum(returned, ZERO) if returned else None,
        net_result=sum(net_values, ZERO) if net_values else None,
        wins=wins,
        losses=losses,
        break_even=break_even,
        average_net=average_net,
        net_sample_std_dev=net_sample_std_dev,
        longest_loss_streak=longest_loss,
        max_drawdown=max_drawdown,
        cumulative_net_curve=tuple(curve),
        drawdown_complete=all_net_known,
    )
