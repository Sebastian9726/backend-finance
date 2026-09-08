"""Dependencias compartidas de FastAPI."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False para poder devolver un 401 con nuestro propio mensaje.
_bearer = HTTPBearer(auto_error=False)

_CREDENCIALES_INVALIDAS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciales invalidas o sesion expirada",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _CREDENCIALES_INVALIDAS

    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise _CREDENCIALES_INVALIDAS

    user = await db.get(User, user_id)
    if user is None or not user.activo:
        raise _CREDENCIALES_INVALIDAS

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
