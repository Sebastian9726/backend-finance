"""Aislamiento por usuario.

Toda consulta de lista debe pasar por `owned()`, y toda mutacion que reciba un
id de recurso debe pasar por `get_owned_or_404()`. Sin esto aparece el IDOR
silencioso: la API responde 200 con datos de otra persona porque nadie filtro
por `user_id`.

Se responde 404 y no 403 cuando el recurso es de otro usuario: un 403 confirma
que ese id existe, y esa confirmacion ya es informacion que no le corresponde.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


def owned[M: Base](model: type[M], user_id: uuid.UUID) -> Select[tuple[M]]:
    """SELECT ya restringido a las filas del usuario."""
    return select(model).where(model.user_id == user_id)  # type: ignore[attr-defined]


async def get_owned_or_404[M: Base](
    db: AsyncSession,
    model: type[M],
    resource_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    detail: str = "Recurso no encontrado",
) -> M:
    """Carga un recurso propio, o lanza 404 si no existe o es de otro usuario."""
    result = await db.execute(owned(model, user_id).where(model.id == resource_id))  # type: ignore[attr-defined]
    instance = result.scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return instance
