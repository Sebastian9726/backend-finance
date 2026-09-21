"""Modelos de la aplicacion.

Se importan todos aqui para que `Base.metadata` los conozca al momento de
correr `alembic revision --autogenerate`.
"""

from app.models.enums import (
    AccountType,
    AssetType,
    CategoryType,
    Currency,
    LiabilityType,
    TransactionType,
)
from app.models.finance import Account, Category, ExchangeRate, Transaction
from app.models.networth import (
    Asset,
    AssetValuation,
    Liability,
    LiabilityBalance,
    NetWorthSnapshot,
)
from app.models.planning import Budget, Goal, GoalContribution
from app.models.user import RefreshToken, User

__all__ = [
    "Account",
    "AccountType",
    "Asset",
    "AssetType",
    "AssetValuation",
    "Budget",
    "Category",
    "CategoryType",
    "Currency",
    "ExchangeRate",
    "Goal",
    "GoalContribution",
    "Liability",
    "LiabilityBalance",
    "LiabilityType",
    "NetWorthSnapshot",
    "RefreshToken",
    "Transaction",
    "TransactionType",
    "User",
]
