"""Endpoints de reportes y tasas de cambio."""

from datetime import date

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.models import CategoryType, Currency
from app.schemas.finance import ExchangeRateCreate, ExchangeRateOut, RateLookup
from app.schemas.reports import CashflowPoint, CategoryBreakdown, DashboardSummary
from app.services import exchange_rates as rates_service
from app.services import reports as service

router = APIRouter(tags=["reportes"])


@router.get("/reports/summary", response_model=DashboardSummary)
async def summary(
    user: CurrentUser, db: DbSession, desde: date, hasta: date
) -> DashboardSummary:
    """Cifras del encabezado del tablero para un periodo."""
    return await service.summary(db, user, desde, hasta)


@router.get("/reports/cashflow", response_model=list[CashflowPoint])
async def cashflow(
    user: CurrentUser, db: DbSession, desde: date, hasta: date
) -> list[CashflowPoint]:
    """Ingresos contra gastos, mes a mes. Excluye transferencias."""
    return await service.cashflow(db, user, desde, hasta)


@router.get("/reports/by-category", response_model=CategoryBreakdown)
async def by_category(
    user: CurrentUser,
    db: DbSession,
    desde: date,
    hasta: date,
    tipo: CategoryType = CategoryType.GASTO,
) -> CategoryBreakdown:
    return await service.by_category(db, user, tipo, desde, hasta)


# --- Tasas de cambio -------------------------------------------------------


@router.get("/exchange-rates", response_model=list[ExchangeRateOut])
async def list_rates(
    user: CurrentUser, db: DbSession, limit: int = Query(default=100, ge=1, le=500)
) -> list[ExchangeRateOut]:
    return await rates_service.list_rates(db, limit)


@router.post(
    "/exchange-rates", response_model=ExchangeRateOut, status_code=status.HTTP_201_CREATED
)
async def upsert_rate(
    data: ExchangeRateCreate, user: CurrentUser, db: DbSession
) -> ExchangeRateOut:
    """Registra o corrige la tasa de un dia. Las tasas son globales."""
    return await rates_service.upsert_rate(db, data)


@router.get("/exchange-rates/lookup", response_model=RateLookup)
async def lookup_rate(
    user: CurrentUser,
    db: DbSession,
    origen: Currency,
    destino: Currency,
    fecha: date,
) -> RateLookup:
    """Tasa aplicable a una fecha. `estimada=true` avisa que se uso la de un
    dia anterior porque no habia tasa para el dia pedido."""
    return await rates_service.get_rate(db, origen, destino, fecha)
