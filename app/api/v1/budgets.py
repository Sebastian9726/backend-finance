"""Endpoints de presupuestos."""

import uuid

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.planning import (
    BudgetCopyResult,
    BudgetCreate,
    BudgetOut,
    BudgetStatus,
    BudgetUpdate,
)
from app.services import budgets as service

router = APIRouter(prefix="/budgets", tags=["presupuestos"])

Anio = Query(ge=2000, le=2200)
Mes = Query(ge=1, le=12)


@router.get("", response_model=list[BudgetOut])
async def list_budgets(
    user: CurrentUser, db: DbSession, anio: int = Anio, mes: int = Mes
) -> list[BudgetOut]:
    return await service.list_budgets(db, user.id, anio, mes)


@router.post("", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(data: BudgetCreate, user: CurrentUser, db: DbSession) -> BudgetOut:
    """Solo se presupuestan categorias de gasto: no se 'limita' lo que entra."""
    return await service.create_budget(db, user.id, data)


@router.get("/status", response_model=BudgetStatus)
async def budget_status(
    user: CurrentUser, db: DbSession, anio: int = Anio, mes: int = Mes
) -> BudgetStatus:
    """Ejecutado contra limite del mes, con el semaforo ya resuelto.

    Incluye aparte las categorias con gasto pero SIN presupuesto, para que no
    pase lo de siempre: presupuestar tres, sentirse cubierto y no ver el gasto
    de las otras diez.
    """
    return await service.status_del_mes(db, user, anio, mes)


@router.post("/copy", response_model=BudgetCopyResult)
async def copy_budgets(
    user: CurrentUser, db: DbSession, anio: int = Anio, mes: int = Mes
) -> BudgetCopyResult:
    """Copia los limites del mes anterior al mes indicado.

    Las categorias que ya tienen presupuesto ese mes no se tocan, asi que
    repetir la llamada no pisa un limite ya ajustado a mano.
    """
    return await service.copiar_del_mes_anterior(db, user.id, anio, mes)


@router.patch("/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: uuid.UUID, data: BudgetUpdate, user: CurrentUser, db: DbSession
) -> BudgetOut:
    return await service.update_budget(db, user.id, budget_id, data)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_budget(db, user.id, budget_id)
