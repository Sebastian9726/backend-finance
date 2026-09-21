"""Presupuestos mensuales y metas de ahorro

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20

No crea ningun ENUM nuevo: `currency` ya existe desde 0002 y solo se
referencia con `create_type=False`. Por eso el downgrade tampoco borra tipos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


currency = postgresql.ENUM("COP", "USD", name="currency", create_type=False)

MONEY = sa.Numeric(precision=18, scale=2)
PERCENT = sa.Numeric(precision=5, scale=1)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("anio", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("monto_limite", MONEY, nullable=False),
        sa.Column("alerta_pct", PERCENT, nullable=False),
        *_timestamps(),
        sa.CheckConstraint("mes BETWEEN 1 AND 12", name=op.f("ck_budgets_mes_valido")),
        sa.CheckConstraint(
            "anio BETWEEN 2000 AND 2200", name=op.f("ck_budgets_anio_razonable")
        ),
        sa.CheckConstraint("monto_limite > 0", name=op.f("ck_budgets_limite_positivo")),
        sa.CheckConstraint(
            "alerta_pct > 0 AND alerta_pct <= 100", name=op.f("ck_budgets_alerta_valida")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_budgets_category_id_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_budgets_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint(
            "user_id", "category_id", "anio", "mes", name="uq_budgets_user_id_category_id_anio_mes"
        ),
    )
    op.create_index("ix_budgets_user_id_anio_mes", "budgets", ["user_id", "anio", "mes"])

    op.create_table(
        "goals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("moneda", currency, nullable=False),
        sa.Column("monto_objetivo", MONEY, nullable=False),
        sa.Column("fecha_objetivo", sa.Date(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("monto_objetivo > 0", name=op.f("ck_goals_objetivo_positivo")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_goals_account_id_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_goals_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goals")),
        sa.UniqueConstraint("user_id", "nombre", name="uq_goals_user_id_nombre"),
    )
    op.create_index("ix_goals_user_id_activa", "goals", ["user_id", "activa"])

    op.create_table(
        "goal_contributions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("goal_id", sa.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("monto", MONEY, nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("monto <> 0", name=op.f("ck_goal_contributions_monto_no_cero")),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goals.id"],
            name=op.f("fk_goal_contributions_goal_id_goals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_goal_contributions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goal_contributions")),
    )
    op.create_index(
        "ix_goal_contributions_goal_id_fecha", "goal_contributions", ["goal_id", "fecha"]
    )
    op.create_index("ix_goal_contributions_user_id", "goal_contributions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_goal_contributions_user_id", table_name="goal_contributions")
    op.drop_index("ix_goal_contributions_goal_id_fecha", table_name="goal_contributions")
    op.drop_table("goal_contributions")

    op.drop_index("ix_goals_user_id_activa", table_name="goals")
    op.drop_table("goals")

    op.drop_index("ix_budgets_user_id_anio_mes", table_name="budgets")
    op.drop_table("budgets")
