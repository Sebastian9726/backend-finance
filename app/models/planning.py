"""Planeacion: presupuestos mensuales y metas de ahorro.

Dos decisiones de moneda, distintas a proposito:

- **El presupuesto vive en la moneda base.** Un limite es un plan ("500.000 al
  mes en mercado"), no un movimiento, y se compara contra el `monto_base` que
  cada transaccion ya trae congelado. Asi un gasto en dolares entra al
  presupuesto por lo que costo el dia que se hizo, sin reconvertir nada.
- **La meta lleva su propia moneda** y todos sus aportes van en ella. Ahorrar
  para un viaje en dolares es legitimo, y manteniendo una sola moneda por meta
  la proyeccion no tiene que cruzar tasas en ningun punto.

Como en la Fase 2, lo acumulado de una meta NO es una columna: es la suma de
sus aportes. Una columna denormalizada se desincroniza al corregir un aporte
viejo y el error solo se nota cuando la proyeccion ya lleva meses mintiendo.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Currency
from app.models.finance import MONEY, _enum

# Porcentaje de alerta: 0-100 con un decimal basta (80.0, 92.5).
PERCENT = Numeric(5, 1)


class Budget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cuanto se piensa gastar en una categoria durante un mes.

    Una fila por categoria y mes. El `anio`/`mes` van como enteros y no como
    una fecha porque un presupuesto no ocurre un dia: es el mes entero, y
    guardarlo como fecha invitaria a compararlo con `<=` contra dias sueltos.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "category_id", "anio", "mes", name="uq_budgets_user_id_category_id_anio_mes"
        ),
        Index("ix_budgets_user_id_anio_mes", "user_id", "anio", "mes"),
        CheckConstraint("mes BETWEEN 1 AND 12", name="mes_valido"),
        CheckConstraint("anio BETWEEN 2000 AND 2200", name="anio_razonable"),
        CheckConstraint("monto_limite > 0", name="limite_positivo"),
        CheckConstraint("alerta_pct > 0 AND alerta_pct <= 100", name="alerta_valida"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Si se borra la categoria se borra su presupuesto: un limite sin categoria
    # no significa nada.
    category_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )

    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int] = mapped_column(Integer, nullable=False)

    monto_limite: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # A partir de que porcentaje del limite se considera "en alerta".
    alerta_pct: Mapped[Decimal] = mapped_column(
        PERCENT, default=Decimal("80"), nullable=False
    )


class Goal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una meta de ahorro. Lo acumulado sale de `goal_contributions`."""

    __tablename__ = "goals"
    __table_args__ = (
        UniqueConstraint("user_id", "nombre", name="uq_goals_user_id_nombre"),
        Index("ix_goals_user_id_activa", "user_id", "activa"),
        CheckConstraint("monto_objetivo > 0", name="objetivo_positivo"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    moneda: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)
    monto_objetivo: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fecha_objetivo: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Cuenta donde se guarda el ahorro. Informativa: no se deduce el avance de
    # su saldo, porque esa cuenta casi siempre tiene tambien dinero de otras
    # cosas y el avance quedaria inflado.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)

    aportes: Mapped[list["GoalContribution"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )


class GoalContribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un abono a la meta, en la moneda de la meta.

    A diferencia de las valuaciones de un activo, aqui SI puede haber varios
    el mismo dia: son eventos que se acumulan, no fotos que se reemplazan. Por
    eso no lleva restriccion unica sobre la fecha.

    El monto admite negativos: sacar plata de la meta es un retiro, y negarlo
    obligaria a borrar aportes para reflejarlo, perdiendo la historia.
    """

    __tablename__ = "goal_contributions"
    __table_args__ = (
        Index("ix_goal_contributions_goal_id_fecha", "goal_id", "fecha"),
        Index("ix_goal_contributions_user_id", "user_id"),
        CheckConstraint("monto <> 0", name="monto_no_cero"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    nota: Mapped[str | None] = mapped_column(String(255), nullable=True)

    goal: Mapped["Goal"] = relationship(back_populates="aportes")
