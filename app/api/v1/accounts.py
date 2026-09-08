"""Endpoints de cuentas."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.finance import AccountCreate, AccountOut, AccountUpdate
from app.services import accounts as service

router = APIRouter(prefix="/accounts", tags=["cuentas"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(
    user: CurrentUser, db: DbSession, solo_activas: bool = False
) -> list[AccountOut]:
    return await service.list_accounts(db, user.id, solo_activas=solo_activas)


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(data: AccountCreate, user: CurrentUser, db: DbSession) -> AccountOut:
    return await service.create_account(db, user.id, data)


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AccountOut:
    return await service.get_account(db, user.id, account_id)


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID, data: AccountUpdate, user: CurrentUser, db: DbSession
) -> AccountOut:
    return await service.update_account(db, user.id, account_id, data)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_account(db, user.id, account_id)
