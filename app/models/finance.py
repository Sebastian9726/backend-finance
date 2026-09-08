"""Nucleo transaccional: cuentas, categorias, transacciones y tasas de cambio."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AccountType, CategoryType, Currency, TransactionType

# NUMERIC(18,2) para montos y NUMERIC(18,8) para tasas. Nunca DOUBLE PRECISION:
# la suma de dobles acumula error y en dinero eso es un defecto, no un detalle.
MONEY = Numeric(18, 2)
RATE = Numeric(18, 8)


def _enum(enum_cls: type, name: str) -> Enum:
    """ENUM nativo de Postgres que guarda el valor, no el nombre del miembro."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class Account(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cuenta donde vive el dinero: efectivo, banco, tarjeta o inversion."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "nombre", name="uq_accounts_user_id_nombre"),
        Index("ix_accounts_user_id_activa", "user_id", "activa"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[AccountType] = mapped_column(_enum(AccountType, "account_type"), nullable=False)
    moneda: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)

    # El saldo vigente NO se guarda: es saldo_inicial + SUM(transacciones),
    # calculado en SQL. Una columna denormalizada se desincroniza en cuanto se
    # edita o borra una transaccion.
    saldo_inicial: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)

    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Categoria de ingreso o gasto, con un nivel opcional de subcategoria."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "nombre", "tipo", name="uq_categories_user_id_nombre_tipo"),
        Index("ix_categories_user_id_tipo", "user_id", "tipo"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[CategoryType] = mapped_column(_enum(CategoryType, "category_type"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    icono: Mapped[str | None] = mapped_column(String(60), nullable=True)

    parent: Mapped["Category | None"] = relationship(remote_side="Category.id")


class Transaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un movimiento de dinero.

    El `monto` es CON SIGNO y representa su efecto sobre el saldo de la cuenta:
    positivo en un ingreso, negativo en un gasto. Una transferencia genera dos
    filas ligadas por `transfer_group_id`, una negativa (sale) y otra positiva
    (entra). Asi el saldo es literalmente `saldo_inicial + SUM(monto)` y no hay
    que recordar en cada consulta que signo aplicar a cada tipo.

    Los CHECK del final impiden que la base acepte un gasto positivo o un
    ingreso negativo, que serian invisibles hasta que un reporte diera raro.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "(tipo = 'ingreso' AND monto > 0) "
            "OR (tipo = 'gasto' AND monto < 0) "
            "OR (tipo = 'transferencia' AND monto <> 0)",
            name="monto_signo_coherente_con_tipo",
        ),
        CheckConstraint(
            "(monto >= 0) = (monto_base >= 0)",
            name="monto_base_mismo_signo",
        ),
        CheckConstraint("tasa_a_base > 0", name="tasa_positiva"),
        # Indice principal: el listado siempre es "las transacciones de este
        # usuario, de mas reciente a mas antigua".
        Index("ix_transactions_user_id_fecha", "user_id", "fecha"),
        Index("ix_transactions_user_id_account_id", "user_id", "account_id"),
        Index("ix_transactions_user_id_category_id", "user_id", "category_id"),
        Index("ix_transactions_transfer_group_id", "transfer_group_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )

    tipo: Mapped[TransactionType] = mapped_column(
        _enum(TransactionType, "transaction_type"), nullable=False
    )

    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    moneda: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)

    # La tasa se CONGELA al registrar el movimiento. Los reportes suman
    # `monto_base` y nunca reconvierten: si se recalculara con la tasa de hoy,
    # el historico cambiaria cada vez que se mueve el dolar.
    tasa_a_base: Mapped[Decimal] = mapped_column(RATE, default=Decimal("1"), nullable=False)
    monto_base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(255), nullable=False)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Liga las dos patas de una transferencia.
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    account: Mapped["Account"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship()


class ExchangeRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tasa de cambio por fecha.

    Es global, no por usuario: el dolar vale lo mismo para todos. La consulta
    busca la tasa mas reciente con fecha <= la buscada, de modo que no hace
    falta cargar todos los dias del calendario.
    """

    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint(
            "fecha", "origen", "destino", name="uq_exchange_rates_fecha_origen_destino"
        ),
        Index("ix_exchange_rates_origen_destino_fecha", "origen", "destino", "fecha"),
        CheckConstraint("tasa > 0", name="tasa_positiva"),
    )

    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    origen: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)
    destino: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)
    tasa: Mapped[Decimal] = mapped_column(RATE, nullable=False)
