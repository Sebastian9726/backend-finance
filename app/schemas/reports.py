"""Schemas de reportes."""

import uuid
from datetime import date

from app.models.enums import CategoryType, Currency
from app.schemas.base import Money, Schema


class CashflowPoint(Schema):
    """Un mes de flujo de caja. `gastos` viene en positivo para graficarlo."""

    mes: date  # primer dia del mes
    ingresos: Money
    gastos: Money
    neto: Money


class CategorySlice(Schema):
    category_id: uuid.UUID | None
    nombre: str
    color: str | None
    total: Money
    # Participacion sobre el total del periodo. Es un porcentaje para pintar,
    # no dinero: no vuelve a entrar en ningun calculo de montos.
    porcentaje: float


class CategoryBreakdown(Schema):
    tipo: CategoryType
    desde: date
    hasta: date
    total: Money
    items: list[CategorySlice]


class DashboardSummary(Schema):
    """Cifras del encabezado del tablero, todas en la moneda base."""

    moneda_base: Currency
    desde: date
    hasta: date
    ingresos: Money
    gastos: Money
    neto: Money
    saldo_total: Money
    transacciones: int
