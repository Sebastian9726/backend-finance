"""Patrimonio: activos, deudas y la foto mensual del patrimonio neto.

Tres ideas sostienen este modulo:

1. **El valor vigente de un activo no se guarda como columna.** Es el valor de
   su valuacion mas reciente. Es la misma decision que con el saldo de una
   cuenta: una columna denormalizada se desincroniza en cuanto se corrige una
   valuacion pasada, y el error solo aparece meses despues, cuando la grafica
   historica ya se ve rara y nadie sabe por que. Lo mismo con el saldo de una
   deuda.

2. **La serie historica SI se materializa** en `net_worth_snapshots`. Es la
   excepcion deliberada a lo anterior y por la razon contraria: recomputar doce
   meses al vuelo exige, por cada mes y cada activo, buscar su ultima valuacion
   y la tasa de cambio de esa fecha. La grafica tiene que ser estable y rapida,
   asi que se calcula una vez y se guarda. Cuando algo cambia se recalcula
   desde la fecha afectada hacia adelante, no todo.

3. **Cada foto congela su propia tasa de cambio.** Un apartamento en USD en
   marzo de 2025 vale, en pesos, lo que valia con el dolar de marzo de 2025.
   Reconvertir el historico con la tasa de hoy haria que el patrimonio del año
   pasado cambiara cada vez que se mueve el dolar.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AssetType, Currency, LiabilityType
from app.models.finance import MONEY, RATE, _enum

# Las tasas de interes van en porcentaje anual con 4 decimales (12.7500 %).
PERCENT = Numeric(9, 4)


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Algo que se tiene y vale dinero: un inmueble, un carro, un CDT.

    No lleva `valor_actual`: ese dato vive en `asset_valuations` y el vigente es
    el de la valuacion mas reciente. Al crear un activo se escribe siempre su
    primera valuacion, de modo que un activo nunca existe sin valor.
    """

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("user_id", "nombre", name="uq_assets_user_id_nombre"),
        Index("ix_assets_user_id_activo", "user_id", "activo"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[AssetType] = mapped_column(_enum(AssetType, "asset_type"), nullable=False)
    moneda: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)

    # Lo que costo y cuando se compro. Opcionales: se puede registrar un activo
    # heredado o cuyo costo original ya nadie recuerda, y aun asi seguir su
    # valor en el tiempo.
    costo_adquisicion: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    fecha_adquisicion: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Un activo vendido se desactiva, no se borra: su historia sostiene los
    # snapshots de los meses en que todavia se tenia.
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)

    valuaciones: Mapped[list["AssetValuation"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class AssetValuation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cuanto valia un activo en una fecha.

    Una fila por fecha y activo: revaluar el mismo dia corrige el valor en vez
    de acumular dos verdades para el mismo instante.
    """

    __tablename__ = "asset_valuations"
    __table_args__ = (
        UniqueConstraint("asset_id", "fecha", name="uq_asset_valuations_asset_id_fecha"),
        Index("ix_asset_valuations_asset_id_fecha", "asset_id", "fecha"),
        Index("ix_asset_valuations_user_id", "user_id"),
        CheckConstraint("valor >= 0", name="valor_no_negativo"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    valor: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    nota: Mapped[str | None] = mapped_column(String(255), nullable=True)

    asset: Mapped["Asset"] = relationship(back_populates="valuaciones")


class Liability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una deuda. El saldo vigente sale de `liability_balances`, no de aqui.

    `principal` es lo que se debia al principio y queda fijo; el saldo baja a
    medida que se paga. Guardar los dos como columnas invitaria a que se
    contradigan, asi que solo el primero es columna.
    """

    __tablename__ = "liabilities"
    __table_args__ = (
        UniqueConstraint("user_id", "nombre", name="uq_liabilities_user_id_nombre"),
        Index("ix_liabilities_user_id_activa", "user_id", "activa"),
        CheckConstraint("tasa_interes IS NULL OR tasa_interes >= 0", name="tasa_no_negativa"),
        CheckConstraint(
            "fecha_fin IS NULL OR fecha_inicio IS NULL OR fecha_fin >= fecha_inicio",
            name="fechas_coherentes",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[LiabilityType] = mapped_column(
        _enum(LiabilityType, "liability_type"), nullable=False
    )
    moneda: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)

    principal: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    # Porcentaje anual. Informativo: no se usa para proyectar amortizaciones,
    # que dependerian de supuestos que el usuario no ha dado.
    tasa_interes: Mapped[Decimal | None] = mapped_column(PERCENT, nullable=True)
    cuota_mensual: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    fecha_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)

    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)

    saldos: Mapped[list["LiabilityBalance"]] = relationship(
        back_populates="liability", cascade="all, delete-orphan"
    )


class LiabilityBalance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cuanto se debia en una fecha. Se guarda POSITIVO.

    El signo lo pone quien consolida: los pasivos restan en el patrimonio neto.
    Guardarlos negativos obligaria a recordar el signo en cada consulta, que es
    justo el error que el `monto` con signo de las transacciones evita.
    """

    __tablename__ = "liability_balances"
    __table_args__ = (
        UniqueConstraint(
            "liability_id", "fecha", name="uq_liability_balances_liability_id_fecha"
        ),
        Index("ix_liability_balances_liability_id_fecha", "liability_id", "fecha"),
        Index("ix_liability_balances_user_id", "user_id"),
        CheckConstraint("saldo >= 0", name="saldo_no_negativo"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    liability_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("liabilities.id", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    saldo: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    nota: Mapped[str | None] = mapped_column(String(255), nullable=True)

    liability: Mapped["Liability"] = relationship(back_populates="saldos")


class NetWorthSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Foto del patrimonio al cierre de un mes, ya en moneda base.

    Es la unica cifra derivada que el proyecto guarda en vez de calcular al
    vuelo, y con motivo: reconstruir la serie exige, por cada mes y cada activo
    o deuda, buscar su ultima valuacion y la tasa de cambio de esa fecha. Se
    recalcula desde la fecha afectada hacia adelante cuando algo cambia.

    `tasa_estimada` marca que al menos una conversion de ese mes uso la tasa de
    un dia anterior porque no habia tasa para el cierre. La UI lo advierte en
    vez de presentar el numero como exacto.
    """

    __tablename__ = "net_worth_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "fecha", name="uq_net_worth_snapshots_user_id_fecha"),
        Index("ix_net_worth_snapshots_user_id_fecha", "user_id", "fecha"),
        CheckConstraint(
            "patrimonio_neto = total_activos - total_pasivos",
            name="patrimonio_cuadra",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Siempre el ultimo dia del mes.
    fecha: Mapped[date] = mapped_column(Date, nullable=False)

    total_activos: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_pasivos: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    patrimonio_neto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    moneda_base: Mapped[Currency] = mapped_column(_enum(Currency, "currency"), nullable=False)
    tasa_estimada: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Se guarda la tasa usada para dejar auditable de donde salio la cifra.
    tasa_usd: Mapped[Decimal | None] = mapped_column(RATE, nullable=True)
