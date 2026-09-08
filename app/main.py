"""Punto de entrada de la API de finanzas personales."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando finance-api (entorno=%s)", settings.environment)
    yield
    await engine.dispose()
    logger.info("finance-api detenida")


app = FastAPI(
    title="Finance API",
    description="Ingresos, gastos, activos, deudas y patrimonio neto en el tiempo.",
    version="0.1.0",
    lifespan=lifespan,
    # En produccion la documentacion interactiva queda deshabilitada.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# CORS: lista blanca explicita. Nunca "*" -- es incompatible con
# allow_credentials y dejaria la API abierta a cualquier origen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    """Liveness: responde sin tocar la base de datos."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db", tags=["infra"])
async def health_db() -> dict[str, object]:
    """Readiness: verifica la conexion y que pgvector este instalado."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        result = await conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        vector_version = result.scalar_one_or_none()
    return {"status": "ok", "pgvector": vector_version}
