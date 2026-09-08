"""Habilita la extension pgvector

Revision ID: 0001
Revises:
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # No se elimina la extension: otras tablas podrian depender de ella y
    # borrarla es mas destructivo que dejarla instalada sin uso.
    pass
