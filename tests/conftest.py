"""Configuracion de las pruebas.

Las pruebas corren contra una base de datos aparte (`<db>_test`) construida con
las MIGRACIONES, no con `create_all`. Asi cada corrida verifica de paso que las
migraciones producen el esquema que el codigo espera: un `create_all` pasaria
las pruebas aunque la migracion estuviera rota.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/finance_test"
_ADMIN_URL = settings.database_url.rsplit("/", 1)[0] + "/postgres"

# Orden de truncado irrelevante gracias a CASCADE, pero se listan todas para
# que agregar una tabla nueva y olvidarla salte como fuga entre pruebas.
_TABLAS = "transactions, categories, accounts, refresh_tokens, exchange_rates, users"


async def _crear_base_de_pruebas() -> None:
    admin = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        existe = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = 'finance_test'")
        )
        if not existe:
            await conn.execute(text("CREATE DATABASE finance_test"))
    await admin.dispose()


def _migrar() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("+asyncpg", "+psycopg"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def base_de_pruebas():
    asyncio.run(_crear_base_de_pruebas())
    _migrar()


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DATABASE_URL, poolclass=None)


@pytest.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    session = sessionmaker()
    try:
        yield session
    finally:
        # Cerrar la sesion antes de truncar: si dejara una transaccion abierta,
        # el TRUNCATE se quedaria esperando el lock hasta el timeout.
        await session.close()
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {_TABLAS} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP con la sesion de pruebas inyectada en la app."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Ayudas de alto nivel
# ---------------------------------------------------------------------------

PASSWORD = "clave-de-prueba-123"


class Usuario:
    """Un usuario autenticado, con su cliente listo para llamar la API."""

    def __init__(self, client: AsyncClient, datos: dict, token: str):
        self._client = client
        self.id = datos["id"]
        self.email = datos["email"]
        self.headers = {"Authorization": f"Bearer {token}"}

    async def post(self, url: str, json: dict):
        return await self._client.post(url, json=json, headers=self.headers)

    async def get(self, url: str, params: dict | None = None):
        return await self._client.get(url, params=params, headers=self.headers)

    async def patch(self, url: str, json: dict):
        return await self._client.patch(url, json=json, headers=self.headers)

    async def delete(self, url: str):
        return await self._client.delete(url, headers=self.headers)


async def registrar(client: AsyncClient, email: str, moneda_base: str = "COP") -> Usuario:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "nombre": email.split("@")[0],
            "moneda_base": moneda_base,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Usuario(client, body["user"], body["access_token"])


@pytest.fixture
async def usuario(client: AsyncClient) -> Usuario:
    return await registrar(client, "ana@ejemplo.com")


@pytest.fixture
async def otro_usuario(client: AsyncClient) -> Usuario:
    return await registrar(client, "beto@ejemplo.com")


@pytest.fixture
async def cuenta(usuario: Usuario) -> dict:
    response = await usuario.post(
        "/api/v1/accounts",
        {
            "nombre": "Bancolombia",
            "tipo": "banco",
            "moneda": "COP",
            "saldo_inicial": "1000000.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def categoria_gasto(usuario: Usuario) -> dict:
    """Toma una del set sembrado al registrarse."""
    response = await usuario.get("/api/v1/categories", {"tipo": "gasto"})
    return response.json()[0]


