"""Configuracion de la aplicacion, leida del entorno."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Base de datos ---
    database_url: str = "postgresql+asyncpg://finance:finance_dev_password@localhost:5433/finance"

    # --- Seguridad ---
    jwt_secret_key: str = "cambiar-esto-en-produccion"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Cookie del refresh token. Vacio = host-only (dev sobre localhost).
    cookie_domain: str = ""
    cookie_secure: bool = False
    refresh_cookie_name: str = "finance_refresh"

    # --- CORS ---
    # `NoDecode` evita que pydantic-settings intente parsear el valor del .env
    # como JSON: queremos una lista separada por comas, no '["a","b"]'.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- Negocio ---
    default_base_currency: str = "COP"

    # --- IA (opcionales: si faltan, los modulos de IA responden 503) ---
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    # --- Entorno ---
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Acepta CORS_ORIGINS como lista separada por comas en el .env."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sync_database_url(self) -> str:
        """URL con driver sincrono, para Alembic."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
