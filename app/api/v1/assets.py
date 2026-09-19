"""Endpoints de activos y sus valuaciones."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.networth import (
    AssetCreate,
    AssetDetail,
    AssetOut,
    AssetUpdate,
    ValuationCreate,
    ValuationOut,
)
from app.services import assets as service

router = APIRouter(prefix="/assets", tags=["activos"])


@router.get("", response_model=list[AssetOut])
async def list_assets(
    user: CurrentUser, db: DbSession, solo_activos: bool = False
) -> list[AssetOut]:
    return await service.list_assets(db, user.id, solo_activos=solo_activos)


@router.post("", response_model=AssetDetail, status_code=status.HTTP_201_CREATED)
async def create_asset(data: AssetCreate, user: CurrentUser, db: DbSession) -> AssetDetail:
    """Crea el activo junto con su primera valuacion y rehace la serie de
    patrimonio desde la fecha de ese valor."""
    return await service.create_asset(db, user, data)


@router.get("/{asset_id}", response_model=AssetDetail)
async def get_asset(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AssetDetail:
    return await service.get_asset(db, user.id, asset_id)


@router.patch("/{asset_id}", response_model=AssetDetail)
async def update_asset(
    asset_id: uuid.UUID, data: AssetUpdate, user: CurrentUser, db: DbSession
) -> AssetDetail:
    return await service.update_asset(db, user, asset_id, data)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_asset(db, user, asset_id)


# --- Valuaciones -----------------------------------------------------------


@router.get("/{asset_id}/valuations", response_model=list[ValuationOut])
async def list_valuations(
    asset_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ValuationOut]:
    return await service.list_valuations(db, user.id, asset_id)


@router.post(
    "/{asset_id}/valuations", response_model=ValuationOut, status_code=status.HTTP_201_CREATED
)
async def add_valuation(
    asset_id: uuid.UUID, data: ValuationCreate, user: CurrentUser, db: DbSession
) -> ValuationOut:
    """Registra el valor del activo en una fecha. Si ya habia una valuacion ese
    mismo dia, la corrige en vez de duplicarla."""
    return await service.add_valuation(db, user, asset_id, data)


@router.delete(
    "/{asset_id}/valuations/{valuation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_valuation(
    asset_id: uuid.UUID, valuation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await service.delete_valuation(db, user, asset_id, valuation_id)
