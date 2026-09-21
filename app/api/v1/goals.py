"""Endpoints de metas de ahorro."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.planning import (
    ContributionCreate,
    ContributionOut,
    GoalCreate,
    GoalDetail,
    GoalOut,
    GoalUpdate,
)
from app.services import goals as service

router = APIRouter(prefix="/goals", tags=["metas"])


@router.get("", response_model=list[GoalOut])
async def list_goals(
    user: CurrentUser, db: DbSession, solo_activas: bool = False
) -> list[GoalOut]:
    return await service.list_goals(db, user.id, solo_activas=solo_activas)


@router.post("", response_model=GoalDetail, status_code=status.HTTP_201_CREATED)
async def create_goal(data: GoalCreate, user: CurrentUser, db: DbSession) -> GoalDetail:
    """`monto_inicial` entra como primer aporte, no como columna, para que la
    meta arranque con historia y el ritmo se pueda medir desde el principio."""
    return await service.create_goal(db, user.id, data)


@router.get("/{goal_id}", response_model=GoalDetail)
async def get_goal(goal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> GoalDetail:
    return await service.get_goal(db, user.id, goal_id)


@router.patch("/{goal_id}", response_model=GoalDetail)
async def update_goal(
    goal_id: uuid.UUID, data: GoalUpdate, user: CurrentUser, db: DbSession
) -> GoalDetail:
    return await service.update_goal(db, user.id, goal_id, data)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(goal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.delete_goal(db, user.id, goal_id)


# --- Aportes ---------------------------------------------------------------


@router.get("/{goal_id}/contributions", response_model=list[ContributionOut])
async def list_contributions(
    goal_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> list[ContributionOut]:
    return await service.list_contributions(db, user.id, goal_id)


@router.post(
    "/{goal_id}/contributions", response_model=ContributionOut, status_code=status.HTTP_201_CREATED
)
async def add_contribution(
    goal_id: uuid.UUID, data: ContributionCreate, user: CurrentUser, db: DbSession
) -> ContributionOut:
    """Un abono, o un retiro si el monto es negativo. Varios el mismo dia se
    suman: son eventos, no fotos del estado."""
    return await service.add_contribution(db, user.id, goal_id, data)


@router.delete(
    "/{goal_id}/contributions/{contribution_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_contribution(
    goal_id: uuid.UUID, contribution_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> None:
    await service.delete_contribution(db, user.id, goal_id, contribution_id)
