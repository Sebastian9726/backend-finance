"""Router de la version 1 de la API."""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    assets,
    auth,
    categories,
    liabilities,
    networth,
    reports,
    transactions,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(accounts.router)
api_router.include_router(categories.router)
api_router.include_router(transactions.router)
api_router.include_router(assets.router)
api_router.include_router(liabilities.router)
api_router.include_router(networth.router)
api_router.include_router(reports.router)
