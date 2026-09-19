"""Schemas de patrimonio: activos, deudas y serie de patrimonio neto."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.models.enums import AssetType, Currency, LiabilityType
from app.schemas.base import Money, Rate, Schema

# ---------------------------------------------------------------------------
# Activos
# ---------------------------------------------------------------------------


class AssetCreate(Schema):
    """`valor_actual` es obligatorio porque con el se escribe la primera
    valuacion: un activo sin valor no aporta nada a la serie de patrimonio.

    `fecha_valor` permite registrar hoy un activo que se tiene desde antes, con
    su valor fechado en el pasado, y que los snapshots de esos meses lo
    incluyan. Si se omite, se asume la fecha de adquisicion, y si tampoco hay,
    hoy.
    """

    nombre: str = Field(min_length=1, max_length=120)
    tipo: AssetType
    moneda: Currency
    valor_actual: Decimal = Field(ge=0)
    fecha_valor: date | None = None
    costo_adquisicion: Decimal | None = Field(default=None, ge=0)
    fecha_adquisicion: date | None = None
    notas: str | None = None


class AssetUpdate(Schema):
    """La moneda no se puede cambiar: las valuaciones ya registradas quedaron
    expresadas en ella y reinterpretarlas alteraria el historico."""

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    tipo: AssetType | None = None
    costo_adquisicion: Decimal | None = Field(default=None, ge=0)
    fecha_adquisicion: date | None = None
    activo: bool | None = None
    notas: str | None = None


class ValuationCreate(Schema):
    fecha: date
    valor: Decimal = Field(ge=0)
    nota: str | None = Field(default=None, max_length=255)


class ValuationOut(Schema):
    id: uuid.UUID
    fecha: date
    valor: Money
    nota: str | None


class AssetOut(Schema):
    id: uuid.UUID
    nombre: str
    tipo: AssetType
    moneda: Currency
    costo_adquisicion: Money | None
    fecha_adquisicion: date | None
    activo: bool
    notas: str | None

    # Derivados de `asset_valuations`, no columnas de `assets`.
    valor_actual: Money
    fecha_valor: date | None
    # Diferencia contra el costo de adquisicion. None cuando no se registro
    # costo: sin punto de partida no hay ganancia que calcular.
    ganancia: Money | None


class AssetDetail(AssetOut):
    valuaciones: list[ValuationOut]


# ---------------------------------------------------------------------------
# Deudas
# ---------------------------------------------------------------------------


class LiabilityCreate(Schema):
    """`saldo_actual` va POSITIVO: es lo que se debe, no un monto con signo.
    El signo lo pone la consolidacion del patrimonio, que resta los pasivos."""

    nombre: str = Field(min_length=1, max_length=120)
    tipo: LiabilityType
    moneda: Currency
    saldo_actual: Decimal = Field(ge=0)
    fecha_saldo: date | None = None
    principal: Decimal | None = Field(default=None, ge=0)
    tasa_interes: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"))
    cuota_mensual: Decimal | None = Field(default=None, ge=0)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    notas: str | None = None

    @model_validator(mode="after")
    def fechas_coherentes(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de fin no puede ser anterior a la de inicio")
        return self


class LiabilityUpdate(Schema):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    tipo: LiabilityType | None = None
    principal: Decimal | None = Field(default=None, ge=0)
    tasa_interes: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"))
    cuota_mensual: Decimal | None = Field(default=None, ge=0)
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    activa: bool | None = None
    notas: str | None = None


class BalanceCreate(Schema):
    fecha: date
    saldo: Decimal = Field(ge=0)
    nota: str | None = Field(default=None, max_length=255)


class BalanceOut(Schema):
    id: uuid.UUID
    fecha: date
    saldo: Money
    nota: str | None


class LiabilityOut(Schema):
    id: uuid.UUID
    nombre: str
    tipo: LiabilityType
    moneda: Currency
    principal: Money | None
    tasa_interes: Decimal | None
    cuota_mensual: Money | None
    fecha_inicio: date | None
    fecha_fin: date | None
    activa: bool
    notas: str | None

    # Derivados de `liability_balances`.
    saldo_actual: Money
    fecha_saldo: date | None
    # Cuanto se ha abonado desde el principal. None si no se registro principal.
    abonado: Money | None


class LiabilityDetail(LiabilityOut):
    saldos: list[BalanceOut]


# ---------------------------------------------------------------------------
# Patrimonio neto
# ---------------------------------------------------------------------------


class NetWorthPoint(Schema):
    """Un mes de la serie. `fecha` es siempre el ultimo dia del mes."""

    fecha: date
    total_activos: Money
    total_pasivos: Money
    patrimonio_neto: Money
    # Avisa que alguna conversion uso la tasa de un dia anterior al cierre.
    tasa_estimada: bool


class NetWorthSeries(Schema):
    moneda_base: Currency
    desde: date
    hasta: date
    puntos: list[NetWorthPoint]

    # Variacion entre el primer y el ultimo punto de la serie. Se calcula en el
    # servicio para que la UI no tenga que restar dinero en JavaScript.
    variacion: Money
    variacion_pct: float | None


class CompositionItem(Schema):
    """Una linea del desglose actual, ya convertida a moneda base."""

    id: uuid.UUID
    nombre: str
    tipo: str
    moneda: Currency
    # Valor en su propia moneda, tal como se registro.
    valor_original: Money
    # El mismo valor llevado a la moneda base con la tasa de hoy.
    valor_base: Money
    fecha_valor: date | None
    porcentaje: float


class NetWorthComposition(Schema):
    """Fotografia de hoy: de que esta hecho el patrimonio ahora mismo."""

    moneda_base: Currency
    fecha: date
    total_activos: Money
    total_pasivos: Money
    patrimonio_neto: Money
    activos: list[CompositionItem]
    pasivos: list[CompositionItem]
    tasa_estimada: bool
    tasa_usd: Rate | None


class RecomputeResult(Schema):
    """Resultado de rematerializar la serie."""

    desde: date
    hasta: date
    meses: int
