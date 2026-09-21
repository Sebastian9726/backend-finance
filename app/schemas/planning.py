"""Schemas de presupuestos y metas."""

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from app.models.enums import Currency
from app.schemas.base import Money, Schema

# ---------------------------------------------------------------------------
# Presupuestos
# ---------------------------------------------------------------------------


class BudgetState(StrEnum):
    """Semaforo de un presupuesto. Se calcula en el backend y no en la UI para
    que el umbral viva en un solo sitio."""

    OK = "ok"
    ALERTA = "alerta"
    EXCEDIDO = "excedido"


class BudgetCreate(Schema):
    category_id: uuid.UUID
    anio: int = Field(ge=2000, le=2200)
    mes: int = Field(ge=1, le=12)
    monto_limite: Decimal = Field(gt=0)
    alerta_pct: Decimal = Field(default=Decimal("80"), gt=0, le=100)


class BudgetUpdate(Schema):
    monto_limite: Decimal | None = Field(default=None, gt=0)
    alerta_pct: Decimal | None = Field(default=None, gt=0, le=100)


class BudgetOut(Schema):
    id: uuid.UUID
    category_id: uuid.UUID
    anio: int
    mes: int
    monto_limite: Money
    alerta_pct: Decimal


class BudgetLine(Schema):
    """Un presupuesto con su ejecucion del mes."""

    id: uuid.UUID
    category_id: uuid.UUID
    categoria: str
    color: str | None
    monto_limite: Money
    ejecutado: Money
    # Lo que queda. Negativo cuando ya se paso del limite: el signo dice de un
    # vistazo si sobra o falta, sin tener que comparar dos numeros.
    disponible: Money
    porcentaje: float
    alerta_pct: Decimal
    estado: BudgetState


class UnbudgetedLine(Schema):
    """Categoria con gasto del mes pero sin presupuesto.

    Se reporta aparte para que no pase lo de siempre: presupuestar tres
    categorias, sentirse cubierto, y no ver el gasto de las otras diez.
    """

    category_id: uuid.UUID | None
    categoria: str
    color: str | None
    ejecutado: Money


class BudgetStatus(Schema):
    moneda_base: Currency
    anio: int
    mes: int
    desde: date
    hasta: date

    total_limite: Money
    total_ejecutado: Money
    total_disponible: Money
    # Gasto del mes que ningun presupuesto cubre.
    total_sin_presupuesto: Money

    lineas: list[BudgetLine]
    sin_presupuesto: list[UnbudgetedLine]


class BudgetCopyResult(Schema):
    """Resultado de copiar el presupuesto de un mes a otro."""

    anio: int
    mes: int
    copiados: int
    # Categorias que ya tenian presupuesto ese mes y no se tocaron.
    omitidos: int


# ---------------------------------------------------------------------------
# Metas
# ---------------------------------------------------------------------------


class GoalCreate(Schema):
    nombre: str = Field(min_length=1, max_length=120)
    moneda: Currency
    monto_objetivo: Decimal = Field(gt=0)
    fecha_objetivo: date | None = None
    account_id: uuid.UUID | None = None
    # Lo ya ahorrado al crear la meta. Si viene, se registra como primer aporte.
    monto_inicial: Decimal | None = Field(default=None, ge=0)
    notas: str | None = None


class GoalUpdate(Schema):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    monto_objetivo: Decimal | None = Field(default=None, gt=0)
    fecha_objetivo: date | None = None
    account_id: uuid.UUID | None = None
    activa: bool | None = None
    notas: str | None = None


class ContributionCreate(Schema):
    fecha: date
    # Admite negativo: sacar plata de la meta es un retiro, no un error.
    monto: Decimal
    nota: str | None = Field(default=None, max_length=255)


class ContributionOut(Schema):
    id: uuid.UUID
    fecha: date
    monto: Money
    nota: str | None


class GoalOut(Schema):
    id: uuid.UUID
    nombre: str
    moneda: Currency
    monto_objetivo: Money
    fecha_objetivo: date | None
    account_id: uuid.UUID | None
    activa: bool
    notas: str | None

    # Derivado de los aportes, no una columna.
    monto_actual: Money
    monto_faltante: Money
    porcentaje: float
    cumplida: bool

    # Ritmo observado y proyeccion. Todos `None` mientras no haya con que
    # calcularlos: inventar una fecha de cumplimiento a partir de un solo
    # aporte seria adivinar, no proyectar.
    aporte_mensual_promedio: Money | None
    fecha_proyectada: date | None
    # Cuanto habria que aportar cada mes para llegar a `fecha_objetivo`.
    aporte_mensual_requerido: Money | None
    # La proyeccion cae despues de la fecha objetivo.
    en_riesgo: bool


class GoalDetail(GoalOut):
    aportes: list[ContributionOut]
