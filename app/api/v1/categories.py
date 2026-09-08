"""Endpoints de categorias."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.models import CategoryType
from app.schemas.finance import CategoryCreate, CategoryOut, CategoryUpdate
from app.services import categories as service

router = APIRouter(prefix="/categories", tags=["categorias"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    user: CurrentUser, db: DbSession, tipo: CategoryType | None = None
) -> list[CategoryOut]:
    return await service.list_categories(db, user.id, tipo)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(data: CategoryCreate, user: CurrentUser, db: DbSession) -> CategoryOut:
    return await service.create_category(db, user.id, data)


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID, data: CategoryUpdate, user: CurrentUser, db: DbSession
) -> CategoryOut:
    return await service.update_category(db, user.id, category_id, data)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_category(db, user.id, category_id)
