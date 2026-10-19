"""Ingestion des transitions de la liste d'historique observée par l'extension Firefox.

Principe : l'extension n'affirme JAMAIS qu'une manche est terminée. Elle envoie deux instantanés
successifs de la liste d'historique (valeurs affichées, la plus récente d'abord). Le serveur refait
lui-même l'alignement et n'enregistre que si UN SEUL décalage k explique le changement, c'est-à-dire
si k valeurs sont apparues au début et si toutes les autres valeurs sont restées identiques.

Hypothèse de travail, observée mais non documentée par l'éditeur : une valeur n'entre dans la liste
qu'à la fin de la manche. 23 transitions réelles l'ont soutenue le 18 octobre 2026 (toutes uniques,
aucune ambiguë), sans que cela constitue une preuve.

Limites connues :
* `played_at` reçoit l'heure de DÉTECTION par l'extension (précision d'environ une seconde, plus le
  délai éventuel de la page), pas l'heure exacte de fin de manche. Si k > 1, les k manches reçoivent
  la même heure de détection.
* Aucun identifiant de manche n'existe sur la page : l'identifiant externe est une empreinte des deux
  instantanés (60 valeurs), jamais le multiplicateur seul. Deux manches de même multiplicateur restent
  donc distinctes, et un envoi répété (reprise réseau, deuxième onglet) ne crée aucun doublon.
* Les manches terminées pendant que la page est fermée ou rechargée ne sont pas observables : le
  premier instantané après chargement sert de point de départ et n'est pas enregistré.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from typing import Any

from aviator_lab.storage import DuplicateRoundError, ValidationError, validate_multiplier

SOURCE = "firefox_history_window_v1"
SCHEMA_VERSION = 1
MIN_OVERLAP = 10
MIN_WINDOW = 10
MAX_WINDOW = 60
_MAX_PAST = timedelta(days=2)
_MAX_FUTURE = timedelta(minutes=5)


class PayloadError(ValueError):
    """Le corps reçu n'a pas le format attendu."""


class AlignmentError(ValueError):
    """Les deux instantanés ne s'expliquent pas par un décalage unique : rien n'est enregistré."""

    def __init__(self, kind: str, candidate_shifts: tuple[int, ...] = ()) -> None:
        super().__init__(f"Alignement refusé : {kind}.")
        self.kind = kind
        self.candidate_shifts = candidate_shifts


@dataclass(frozen=True)
class Alignment:
    kind: str  # no_change | shift | ambiguous | unalignable
    shift: int | None
    added: tuple[Decimal, ...]  # plus récente d'abord
    candidate_shifts: tuple[int, ...]


@dataclass(frozen=True)
class IngestResult:
    status: str  # saved | duplicate | no_change
    saved: int
    duplicates: int
    shift: int


def align_history_window(
    previous: list[Decimal], following: list[Decimal], min_overlap: int = MIN_OVERLAP
) -> Alignment:
    """Cherche k tel que `following[k:]` prolonge `previous[:]` (k valeurs insérées au début).

    Même logique que `alignHistoryWindow` de l'extension. Les fenêtres doivent avoir la même taille :
    un changement de taille (chargement progressif, remplacement) n'est jamais interprété comme des manches.
    """
    prev, nxt = list(previous), list(following)
    if prev == nxt:
        return Alignment("no_change", 0, (), (0,))
    if len(prev) != len(nxt):
        return Alignment("unalignable", None, (), ())
    candidates: list[int] = []
    for k in range(1, len(nxt) + 1):
        overlap = min(len(nxt) - k, len(prev))
        if overlap < min(min_overlap, len(prev), len(nxt)):
            break
        if nxt[k : k + overlap] == prev[:overlap]:
            candidates.append(k)
    if len(candidates) == 1:
        k = candidates[0]
        return Alignment("shift", k, tuple(nxt[:k]), tuple(candidates))
    if len(candidates) > 1:
        return Alignment("ambiguous", None, (), tuple(candidates))
    return Alignment("unalignable", None, (), ())


def _window(value: Any, label: str) -> list[Decimal]:
    if not isinstance(value, list) or not MIN_WINDOW <= len(value) <= MAX_WINDOW:
        raise PayloadError(f"{label} doit être une liste de {MIN_WINDOW} à {MAX_WINDOW} valeurs.")
    clean: list[Decimal] = []
    for item in value:
        if not isinstance(item, str):
            raise PayloadError(f"{label} : chaque valeur doit être une chaîne.")
        try:
            clean.append(validate_multiplier(item))
        except ValidationError as exc:
            raise PayloadError(f"{label} : {exc}") from None
    return clean


def _observed_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PayloadError("observed_at doit être une date ISO 8601 avec fuseau.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise PayloadError("observed_at n'est pas une date ISO 8601 valide.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PayloadError("observed_at doit préciser son fuseau horaire.")
    now = datetime.now(timezone.utc)
    if parsed < now - _MAX_PAST or parsed > now + _MAX_FUTURE:
        raise PayloadError("observed_at est trop éloignée de l'heure locale.")
    return parsed.astimezone(timezone.utc)


def transition_fingerprint(previous: list[Decimal], following: list[Decimal]) -> str:
    """Empreinte stable de la paire d'instantanés (valeurs normalisées à quatre décimales)."""
    text = "|".join(f"{v:.4f}" for v in previous) + "#" + "|".join(f"{v:.4f}" for v in following)
    return hashlib.sha1(text.encode("ascii")).hexdigest()


def ingest_history_transition(repository: Any, payload: Any) -> IngestResult:
    """Valide une transition, refait l'alignement et enregistre les manches apparues.

    Lève PayloadError (format) ou AlignmentError (décalage non unique). Une erreur de stockage est
    propagée telle quelle : l'appelant ne doit jamais la présenter comme un succès.
    Les k insertions se font manche par manche; en cas d'interruption, la reprise complète les
    manquantes sans créer de doublon (identifiants externes déterministes).
    """
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise PayloadError(f"Le champ schema doit valoir {SCHEMA_VERSION}.")
    previous = _window(payload.get("previous"), "previous")
    following = _window(payload.get("next"), "next")
    observed_at = _observed_at(payload.get("observed_at"))

    alignment = align_history_window(previous, following)
    if alignment.kind == "no_change":
        return IngestResult("no_change", 0, 0, 0)
    if alignment.kind != "shift":
        raise AlignmentError(alignment.kind, alignment.candidate_shifts)

    fingerprint = transition_fingerprint(previous, following)
    saved = duplicates = 0
    for index, value in enumerate(reversed(alignment.added)):  # la plus ancienne d'abord
        try:
            repository.add_round(
                value,
                source=SOURCE,
                origin="real",
                external_id=f"fhw1:{fingerprint}:{index}",
                played_at=observed_at,
            )
            saved += 1
        except DuplicateRoundError:
            duplicates += 1
    status = "saved" if saved else "duplicate"
    return IngestResult(status, saved, duplicates, alignment.shift or 0)