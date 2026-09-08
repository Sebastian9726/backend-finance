"""Pruebas de humo del andamiaje (Fase 0)."""

from httpx import AsyncClient

from app.core.config import Settings


async def test_health_responde_sin_tocar_la_bd(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_db_reporta_pgvector(client: AsyncClient):
    """Requiere la base de datos de desarrollo arriba (docker compose up -d db)."""
    response = await client.get("/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["pgvector"] is not None, "pgvector no esta instalado en la base de datos"


def test_cors_origins_se_lee_como_lista_separada_por_comas():
    settings = Settings(cors_origins="http://a.com, http://b.com")
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_cors_no_permite_comodin_por_defecto():
    settings = Settings()
    assert "*" not in settings.cors_origins


def test_url_sincrona_para_alembic():
    settings = Settings(database_url="postgresql+asyncpg://u:p@h:5432/d")
    assert settings.sync_database_url == "postgresql+psycopg://u:p@h:5432/d"
