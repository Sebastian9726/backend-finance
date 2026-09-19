"""Endpoints de patrimonio neto."""

from datetime import date

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.schemas.networth import NetWorthComposition, NetWorthSeries, RecomputeResult
from app.services import networth as service

router = APIRouter(prefix="/networth", tags=["patrimonio"])


@router.get("/series", response_model=NetWorthSeries)
async def series(
    user: CurrentUser, db: DbSession, desde: date, hasta: date
) -> NetWorthSeries:
    """Serie mensual de patrimonio neto, ya materializada.

    Solo lee `net_worth_snapshots`: no recalcula. Las mutaciones de activos,
    deudas, valuaciones y saldos ya dejan la serie al dia. Si aun asi hiciera
    falta rehacerla, esta POST /networth/recompute.
    """
    return await service.series(db, user, desde, hasta)


@router.get("/composition", response_model=NetWorthComposition)
async def composition(
    user: CurrentUser, db: DbSession, al: date | None = None
) -> NetWorthComposition:
    """De que esta hecho el patrimonio a una fecha (por defecto, hoy)."""
    return await service.composition(db, user, al)


@router.post("/recompute", response_model=RecomputeResult)
async def recompute(
    user: CurrentUser, db: DbSession, desde: date | None = None
) -> RecomputeResult:
    """Rematerializa los cierres mensuales desde `desde` (o desde la primera
    valuacion del usuario) hasta hoy.

    Normalmente no hace falta llamarlo. Sirve tras cargar tasas de cambio que
    faltaban: los snapshots que se calcularon con una tasa estimada se rehacen
    con la tasa correcta.
    """
    return await service.recompute_snapshots(db, user, desde)
