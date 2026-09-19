"""Endpoints de deudas y sus saldos."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.networth import (
    BalanceCreate,
    BalanceOut,
    LiabilityCreate,
    LiabilityDetail,
    LiabilityOut,
    LiabilityUpdate,
)
from app.services import liabilities as service

router = APIRouter(prefix="/liabilities", tags=["deudas"])


@router.get("", response_model=list[LiabilityOut])
async def list_liabilities(
    user: CurrentUser, db: DbSession, solo_activas: bool = False
) -> list[LiabilityOut]:
    return await service.list_liabilities(db, user.id, solo_activas=solo_activas)


@router.post("", response_model=LiabilityDetail, status_code=status.HTTP_201_CREATED)
async def create_liability(
    data: LiabilityCreate, user: CurrentUser, db: DbSession
) -> LiabilityDetail:
    """Crea la deuda junto con su primer saldo. El saldo va POSITIVO: es lo que
    se debe, y el signo lo pone la consolidacion del patrimonio."""
    return await service.create_liability(db, user, data)


@router.get("/{liability_id}", response_model=LiabilityDetail)
async def get_liability(
    liability_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> LiabilityDetail:
    return await service.get_liability(db, user.id, liability_id)


@router.patch("/{liability_id}", response_model=LiabilityDetail)
async def update_liability(
    liability_id: uuid.UUID, data: LiabilityUpdate, user: CurrentUser, db: DbSession
) -> LiabilityDetail:
    return await service.update_liability(db, user, liability_id, data)


@router.delete("/{liability_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_liability(
    liability_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await service.delete_liability(db, user, liability_id)


# --- Saldos ----------------------------------------------------------------


@router.get("/{liability_id}/balances", response_model=list[BalanceOut])
async def list_balances(
    liability_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[BalanceOut]:
    return await service.list_balances(db, user.id, liability_id)


@router.post(
    "/{liability_id}/balances", response_model=BalanceOut, status_code=status.HTTP_201_CREATED
)
async def add_balance(
    liability_id: uuid.UUID, data: BalanceCreate, user: CurrentUser, db: DbSession
) -> BalanceOut:
    """Registra cuanto se debe en una fecha. Repetir la fecha corrige el saldo
    de ese dia en vez de duplicarlo."""
    return await service.add_balance(db, user, liability_id, data)


@router.delete(
    "/{liability_id}/balances/{balance_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_balance(
    liability_id: uuid.UUID, balance_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await service.delete_balance(db, user, liability_id, balance_id)
