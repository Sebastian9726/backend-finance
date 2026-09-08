"""Registro, login y rotacion de tokens."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import RegisterIn
from app.services.categories import seed_default_categories

_CREDENCIALES = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    # Mismo mensaje para email inexistente y contrasena mala: distinguirlos
    # convierte el login en un detector de cuentas registradas.
    detail="Email o contrasena incorrectos",
)


async def register(db: AsyncSession, data: RegisterIn) -> User:
    user = User(
        id=uuid.uuid4(),
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        nombre=data.nombre,
        moneda_base=data.moneda_base,
    )
    db.add(user)
    try:
        # El INSERT ocurre en el flush, no en el commit: el except tiene que
        # cubrirlo o el conflicto de email escapa como error 500.
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cuenta con ese email",
        ) from exc

    await seed_default_categories(db, user.id)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        raise _CREDENCIALES
    if not user.activo:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta desactivada")

    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, int, str]:
    """Emite un par nuevo. Devuelve (access_token, expires_in, refresh_token).

    Abre una familia nueva: cada login es una cadena de rotaciones distinta, de
    modo que cerrar sesion en un dispositivo no tumba los demas.
    """
    return await _emit(db, user.id, family_id=uuid.uuid4())


async def rotate(db: AsyncSession, presented_token: str) -> tuple[User, str, int, str]:
    """Canjea un refresh token por un par nuevo, con deteccion de reuso.

    Un token revocado que vuelve a presentarse significa que alguien guardo una
    copia: el legitimo ya rotó. No se puede saber si quien lo presenta es el
    atacante o la victima, asi que se revoca la familia completa y ambos
    quedan obligados a autenticarse de nuevo.
    """
    token_hash = hash_refresh_token(presented_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida"
        )

    if stored.revoked_at is not None:
        await _revoke_family(db, stored.user_id, stored.family_id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesion invalidada por seguridad. Inicia sesion de nuevo.",
        )

    if stored.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="La sesion expiro"
        )

    user = await db.get(User, stored.user_id)
    if user is None or not user.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida"
        )

    access_token, expires_in, refresh_token = await _emit(
        db, user.id, family_id=stored.family_id, commit=False
    )

    stored.revoked_at = datetime.now(UTC)
    nuevo = await db.execute(
        select(RefreshToken.id).where(
            RefreshToken.token_hash == hash_refresh_token(refresh_token)
        )
    )
    stored.replaced_by = nuevo.scalar_one()

    await db.commit()
    return user, access_token, expires_in, refresh_token


async def revoke(db: AsyncSession, presented_token: str) -> None:
    """Cierra la sesion. Idempotente: un token desconocido no es un error, el
    resultado deseado (que ese token no sirva) ya se cumple."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(presented_token))
    )
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
        await db.commit()


async def _emit(
    db: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID, *, commit: bool = True
) -> tuple[str, int, str]:
    access_token, expires_in = create_access_token(user_id)
    refresh_token = generate_refresh_token()

    db.add(
        RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=family_id,
            expires_at=refresh_token_expiry(),
        )
    )
    if commit:
        await db.commit()
    else:
        await db.flush()

    return access_token, expires_in, refresh_token


async def _revoke_family(db: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
