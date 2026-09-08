"""Endpoints de autenticacion."""

from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# La cookie del refresh token solo se envia a las rutas de /auth: ningun otro
# endpoint la necesita, y no mandarla reduce la superficie de un CSRF.
_COOKIE_PATH = "/api/v1/auth"

# El alias ata el parametro al nombre configurado de la cookie: sin el, FastAPI
# buscaria una cookie llamada como la variable de Python y cambiar el ajuste
# romperia la lectura en silencio.
# El valor por defecto va en la firma (`token: RefreshCookie = None`), no aqui:
# FastAPI lo exige asi cuando se usa Annotated.
RefreshCookie = Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)]


def _set_refresh_cookie(response: Response, token: str) -> None:
    """El refresh token va en cookie httpOnly para que el JavaScript de la
    pagina no pueda leerlo: si hay un XSS, el atacante no se lleva la sesion
    persistente. El access token, en cambio, vive en memoria y expira en 15
    minutos."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain or None,
        path=_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        domain=settings.cookie_domain or None,
        path=_COOKIE_PATH,
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, response: Response, db: DbSession) -> TokenOut:
    """Crea la cuenta, siembra las categorias iniciales e inicia sesion."""
    user = await auth_service.register(db, data)
    access_token, expires_in, refresh_token = await auth_service.issue_tokens(db, user)

    _set_refresh_cookie(response, refresh_token)
    return TokenOut(
        access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=TokenOut)
async def login(data: LoginIn, response: Response, db: DbSession) -> TokenOut:
    user = await auth_service.authenticate(db, data.email, data.password)
    access_token, expires_in, refresh_token = await auth_service.issue_tokens(db, user)

    _set_refresh_cookie(response, refresh_token)
    return TokenOut(
        access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post("/refresh", response_model=TokenOut)
async def refresh(response: Response, db: DbSession, token: RefreshCookie = None) -> TokenOut:
    """Canjea la cookie por un par nuevo de tokens.

    El frontend llama aqui al cargar la pagina, porque el access token vive en
    memoria y se pierde al recargar.
    """
    if not token:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No hay sesion")

    user, access_token, expires_in, nuevo_refresh = await auth_service.rotate(db, token)

    _set_refresh_cookie(response, nuevo_refresh)
    return TokenOut(
        access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, db: DbSession, token: RefreshCookie = None) -> None:
    if token:
        await auth_service.revoke(db, token)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
