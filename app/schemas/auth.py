"""Schemas de autenticacion."""

import uuid

from pydantic import EmailStr, Field, field_validator

from app.models.enums import Currency
from app.schemas.base import Schema


class RegisterIn(Schema):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    nombre: str = Field(min_length=1, max_length=120)
    moneda_base: Currency = Currency.COP

    @field_validator("password")
    @classmethod
    def password_no_trivial(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("La contrasena debe combinar letras y numeros")
        return v


class LoginIn(Schema):
    email: EmailStr
    password: str


class UserOut(Schema):
    id: uuid.UUID
    email: EmailStr
    nombre: str
    moneda_base: Currency


class TokenOut(Schema):
    """El refresh token NO va aqui: viaja en una cookie httpOnly para que el
    JavaScript de la pagina no pueda leerlo."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
