"""Modèles et accès SQLite pour les données locales du laboratoire."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class ValidationError(ValueError):
    """Valeur refusée avant son insertion dans la base."""


class DuplicateRoundError(ValueError):
    """Une manche portant le même identifiant externe est déjà enregistrée."""


class DuplicateBetError(ValueError):
    """Un pari portant le même identifiant externe est déjà enregistré."""


class Base(DeclarativeBase):
    """Classe de base des tables SQLAlchemy."""


class GameRound(Base):
    """Résultat d'une manche, avec sa provenance et son statut de validation."""

    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="valid")
    invalid_reason: Mapped[str | None] = mapped_column(String(500))


class PersonalBet(Base):
    """Données financières d'un pari personnel; les champs absents restent NULL."""

    __tablename__ = "personal_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("rounds.id"))
    bet_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stake_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    cashout_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    returned_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)


class AnalysisRun(Base):
    """Paramètres et provenance d'une analyse importante ou d'une simulation."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    analysis_type: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_version: Mapped[str] = mapped_column(String(50), nullable=False)
    random_seed: Mapped[int | None] = mapped_column(Integer)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


def validate_multiplier(value: Any) -> Decimal:
    """Convertit un multiplicateur en Decimal; accepte seulement les valeurs finies >= 1."""
    if isinstance(value, bool) or value is None:
        raise ValidationError("Le multiplicateur doit être un nombre supérieur ou égal à 1.")
    try:
        multiplier = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError("Le multiplicateur doit être un nombre valide.") from None
    if not multiplier.is_finite() or multiplier < Decimal("1"):
        raise ValidationError("Le multiplicateur doit être fini et supérieur ou égal à 1.")
    if not math.isfinite(float(multiplier)) or multiplier > Decimal("99999999.9999"):
        raise ValidationError("Le multiplicateur dépasse la précision maximale prise en charge.")
    if multiplier.quantize(Decimal("0.0001")) != multiplier:
        raise ValidationError("Le multiplicateur accepte au maximum quatre décimales.")
    return multiplier


def _validate_timestamp(value: Any, label: str) -> datetime | None:
    """Accepte un datetime parsé ou l'absence de date, jamais une chaîne brute."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValidationError(f"{label} doit être une date et heure ISO 8601 déjà analysée.")
    try:
        value.isoformat()
        value.utcoffset()
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValidationError(f"{label} n'est pas une date et heure valide.") from exc
    return value


def _validate_amount(value: Any, label: str, *, allow_negative: bool = False) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError(f"{label} doit être un montant numérique valide.") from None
    if not amount.is_finite() or (not allow_negative and amount < 0):
        qualifier = "fini" if allow_negative else "fini et positif ou nul"
        raise ValidationError(f"{label} doit être {qualifier}.")
    if amount.copy_abs() > Decimal("9999999999.9999"):
        raise ValidationError(f"{label} dépasse la précision maximale prise en charge.")
    if amount.quantize(Decimal("0.0001")) != amount:
        raise ValidationError(f"{label} accepte au maximum quatre décimales.")
    return amount


class RoundRepository:
    """Enregistre et relit les manches dans une base SQLite locale."""

    def __init__(self, database_url: str | Path) -> None:
        url = str(database_url)
        if "://" not in url:
            path = Path(url).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.resolve()}"
        self.engine = create_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        """Crée les tables manquantes et ajoute les colonnes récentes sans perte."""
        Base.metadata.create_all(self.engine)
        round_columns = {column["name"] for column in inspect(self.engine).get_columns("rounds")}
        if "recorded_at" not in round_columns:
            with self.engine.begin() as connection:
                connection.execute(text("ALTER TABLE rounds ADD COLUMN recorded_at DATETIME"))

    def add_round(
        self,
        multiplier: Any,
        *,
        source: str,
        origin: str = "real",
        external_id: str | None = None,
        played_at: datetime | None = None,
    ) -> GameRound:
        """Valide et enregistre une manche; une erreur n'est jamais ignorée."""
        clean_multiplier = validate_multiplier(multiplier)
        clean_played_at = _validate_timestamp(played_at, "L'horodatage de la manche")
        clean_source = source.strip() if isinstance(source, str) else ""
        if not clean_source:
            raise ValidationError("La source doit être renseignée.")
        if origin not in {"real", "synthetic", "simulated"}:
            raise ValidationError("L'origine doit être real, synthetic ou simulated.")
        clean_external_id = external_id.strip() if external_id else None
        if clean_external_id == "":
            clean_external_id = None

        round_record = GameRound(
            multiplier=clean_multiplier,
            source=clean_source,
            origin=origin,
            external_id=clean_external_id,
            played_at=clean_played_at,
            validation_status="valid",
        )
        try:
            with self._sessions.begin() as session:
                session.add(round_record)
        except IntegrityError as exc:
            if clean_external_id:
                raise DuplicateRoundError(
                    f"L'identifiant externe {clean_external_id!r} existe déjà."
                ) from exc
            raise
        return round_record

    def add_bet(
        self,
        *,
        source: str,
        origin: str = "real",
        external_id: str | None = None,
        round_id: int | None = None,
        bet_at: datetime | None = None,
        stake_amount: Any = None,
        cashout_multiplier: Any = None,
        returned_amount: Any = None,
        net_amount: Any = None,
        currency: str | None = None,
    ) -> PersonalBet:
        """Enregistre un pari personnel sans fabriquer de champs financiers manquants."""
        clean_bet_at = _validate_timestamp(bet_at, "L'horodatage du pari")
        clean_source = source.strip() if isinstance(source, str) else ""
        if not clean_source:
            raise ValidationError("La source du pari doit être renseignée.")
        if origin not in {"real", "synthetic", "simulated"}:
            raise ValidationError("L'origine doit être real, synthetic ou simulated.")
        clean_external_id = external_id.strip() if external_id else None
        if clean_external_id == "":
            clean_external_id = None

        stake = _validate_amount(stake_amount, "La mise")
        if stake is not None and stake <= 0:
            raise ValidationError("La mise doit être strictement positive.")
        returned = _validate_amount(returned_amount, "Le montant retourné")
        net = _validate_amount(net_amount, "Le résultat net", allow_negative=True)
        cashout = None if cashout_multiplier is None else validate_multiplier(cashout_multiplier)
        if all(value is None for value in (stake, returned, net, cashout)):
            raise ValidationError("Au moins une donnée de mise, retrait ou résultat doit être fournie.")
        clean_currency = currency.strip().upper() if currency else None
        if clean_currency and (len(clean_currency) != 3 or not clean_currency.isalpha()):
            raise ValidationError("La devise doit être un code de trois lettres, par exemple MGA.")
        if net is None and stake is not None and returned is not None:
            net = returned - stake
        elif net is not None and stake is not None and returned is not None and net != returned - stake:
            raise ValidationError("Le résultat net doit être égal au montant retourné moins la mise.")

        record = PersonalBet(
            external_id=clean_external_id,
            round_id=round_id,
            bet_at=clean_bet_at,
            stake_amount=stake,
            cashout_multiplier=cashout,
            returned_amount=returned,
            net_amount=net,
            currency=clean_currency,
            source=clean_source,
            origin=origin,
        )
        try:
            with self._sessions.begin() as session:
                if round_id is not None:
                    linked_round = session.get(GameRound, round_id)
                    if linked_round is None:
                        raise ValidationError(f"Manche liée introuvable : {round_id}.")
                    if linked_round.origin != origin:
                        raise ValidationError("Le pari et sa manche liée doivent avoir la même origine.")
                session.add(record)
        except IntegrityError as exc:
            if clean_external_id:
                raise DuplicateBetError(
                    f"L'identifiant de pari {clean_external_id!r} existe déjà."
                ) from exc
            raise
        return record

    def list_rounds(self, *, origin: str | None = None) -> list[GameRound]:
        """Retourne les manches par ordre d'insertion, filtrées par origine si demandé."""
        statement = select(GameRound).order_by(GameRound.id)
        if origin is not None:
            if origin not in {"real", "synthetic", "simulated"}:
                raise ValidationError("Origine de filtre inconnue.")
            statement = statement.where(GameRound.origin == origin)
        with Session(self.engine) as session:
            return list(session.scalars(statement))

    def list_bets(self, *, origin: str | None = None) -> list[PersonalBet]:
        """Retourne les paris par ordre d'insertion, filtrés par origine si demandé."""
        statement = select(PersonalBet).order_by(PersonalBet.id)
        if origin is not None:
            if origin not in {"real", "synthetic", "simulated"}:
                raise ValidationError("Origine de filtre inconnue.")
            statement = statement.where(PersonalBet.origin == origin)
        with Session(self.engine) as session:
            return list(session.scalars(statement))

    def record_analysis(
        self,
        *,
        analysis_type: str,
        code_version: str,
        parameters: dict[str, Any],
        random_seed: int | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> AnalysisRun:
        """Enregistre les paramètres et la période d'une analyse effectuée."""
        if not analysis_type.strip() or not code_version.strip():
            raise ValidationError("Le type d'analyse et la version du code sont requis.")
        if random_seed is not None and (
            isinstance(random_seed, bool) or not isinstance(random_seed, int) or random_seed < 0
        ):
            raise ValidationError("La graine aléatoire doit être un entier positif ou nul.")
        record = AnalysisRun(
            analysis_type=analysis_type,
            code_version=code_version,
            parameters=parameters,
            random_seed=random_seed,
            period_start=period_start,
            period_end=period_end,
        )
        with self._sessions.begin() as session:
            session.add(record)
        return record

    def list_analyses(self) -> list[AnalysisRun]:
        """Retourne les analyses enregistrées par ordre chronologique d'insertion."""
        statement = select(AnalysisRun).order_by(AnalysisRun.id)
        with Session(self.engine) as session:
            return list(session.scalars(statement))

    def close(self) -> None:
        """Libère les connexions ouvertes par SQLAlchemy."""
        self.engine.dispose()
