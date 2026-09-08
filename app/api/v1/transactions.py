"""Endpoints de transacciones."""

import uuid
from datetime import date

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.models import TransactionType
from app.schemas.base import Page
from app.schemas.finance import (
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
    TransferCreate,
)
from app.services import transactions as service

router = APIRouter(prefix="/transactions", tags=["transacciones"])


@router.get("", response_model=Page[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    db: DbSession,
    desde: date | None = None,
    hasta: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    tipo: TransactionType | None = None,
    q: str | None = Query(default=None, description="Busca en descripcion y notas"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[TransactionOut]:
    items, total = await service.list_transactions(
        db,
        user,
        desde=desde,
        hasta=hasta,
        account_id=account_id,
        category_id=category_id,
        tipo=tipo,
        q=q,
        page=page,
        page_size=page_size,
    )
    return Page[TransactionOut](
        items=[TransactionOut.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    data: TransactionCreate, user: CurrentUser, db: DbSession
) -> TransactionOut:
    return TransactionOut.model_validate(await service.create_transaction(db, user, data))


@router.post(
    "/transfer", response_model=list[TransactionOut], status_code=status.HTTP_201_CREATED
)
async def create_transfer(
    data: TransferCreate, user: CurrentUser, db: DbSession
) -> list[TransactionOut]:
    """Mueve dinero entre dos cuentas propias. Devuelve las dos patas."""
    patas = await service.create_transfer(db, user, data)
    return [TransactionOut.model_validate(t) for t in patas]


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(
    transaction_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> TransactionOut:
    return TransactionOut.model_validate(await service.get_transaction(db, user.id, transaction_id))


@router.patch("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    transaction_id: uuid.UUID, data: TransactionUpdate, user: CurrentUser, db: DbSession
) -> TransactionOut:
    return TransactionOut.model_validate(
        await service.update_transaction(db, user, transaction_id, data)
    )


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    """Borra la transaccion. Si es una transferencia, borra sus dos patas."""
    await service.delete_transaction(db, user.id, transaction_id)
