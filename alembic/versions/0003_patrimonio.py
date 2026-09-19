"""Patrimonio: activos, deudas, sus historiales y la foto mensual

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-19

Misma cautela con los ENUM que en 0002: se crean una sola vez al principio con
`create_type=False` en las columnas, y se borran al final del downgrade. El
autogenerate los declararia dentro de cada `create_table`, emitiendo un
`CREATE TYPE` por tabla que falla en la segunda que use `currency`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `currency` ya existe desde 0002: se referencia, no se crea.
currency = postgresql.ENUM("COP", "USD", name="currency", create_type=False)
asset_type = postgresql.ENUM(
    "inmueble", "vehiculo", "inversion", "ahorro", "otro", name="asset_type", create_type=False
)
liability_type = postgresql.ENUM(
    "hipoteca", "vehiculo", "tarjeta", "personal", "otro", name="liability_type",
    create_type=False,
)

# Solo los que nacen en esta revision; `currency` no se toca.
ENUMS_NUEVOS = (asset_type, liability_type)

MONEY = sa.Numeric(precision=18, scale=2)
RATE = sa.Numeric(precision=18, scale=8)
PERCENT = sa.Numeric(precision=9, scale=4)


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
    bind = op.get_bind()
    for enum in ENUMS_NUEVOS:
        enum.create(bind, checkfirst=True)

    # --- Activos -----------------------------------------------------------
    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo", asset_type, nullable=False),
        sa.Column("moneda", currency, nullable=False),
        sa.Column("costo_adquisicion", MONEY, nullable=True),
        sa.Column("fecha_adquisicion", sa.Date(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_assets_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("user_id", "nombre", name="uq_assets_user_id_nombre"),
    )
    op.create_index("ix_assets_user_id_activo", "assets", ["user_id", "activo"])

    op.create_table(
        "asset_valuations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("valor", MONEY, nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("valor >= 0", name=op.f("ck_asset_valuations_valor_no_negativo")),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_asset_valuations_asset_id_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_asset_valuations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_valuations")),
        sa.UniqueConstraint("asset_id", "fecha", name="uq_asset_valuations_asset_id_fecha"),
    )
    op.create_index(
        "ix_asset_valuations_asset_id_fecha", "asset_valuations", ["asset_id", "fecha"]
    )
    op.create_index("ix_asset_valuations_user_id", "asset_valuations", ["user_id"])

    # --- Deudas ------------------------------------------------------------
    op.create_table(
        "liabilities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo", liability_type, nullable=False),
        sa.Column("moneda", currency, nullable=False),
        sa.Column("principal", MONEY, nullable=True),
        sa.Column("tasa_interes", PERCENT, nullable=True),
        sa.Column("cuota_mensual", MONEY, nullable=True),
        sa.Column("fecha_inicio", sa.Date(), nullable=True),
        sa.Column("fecha_fin", sa.Date(), nullable=True),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "tasa_interes IS NULL OR tasa_interes >= 0",
            name=op.f("ck_liabilities_tasa_no_negativa"),
        ),
        sa.CheckConstraint(
            "fecha_fin IS NULL OR fecha_inicio IS NULL OR fecha_fin >= fecha_inicio",
            name=op.f("ck_liabilities_fechas_coherentes"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_liabilities_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_liabilities")),
        sa.UniqueConstraint("user_id", "nombre", name="uq_liabilities_user_id_nombre"),
    )
    op.create_index("ix_liabilities_user_id_activa", "liabilities", ["user_id", "activa"])

    op.create_table(
        "liability_balances",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("liability_id", sa.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("saldo", MONEY, nullable=False),
        sa.Column("nota", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("saldo >= 0", name=op.f("ck_liability_balances_saldo_no_negativo")),
        sa.ForeignKeyConstraint(
            ["liability_id"],
            ["liabilities.id"],
            name=op.f("fk_liability_balances_liability_id_liabilities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_liability_balances_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_liability_balances")),
        sa.UniqueConstraint(
            "liability_id", "fecha", name="uq_liability_balances_liability_id_fecha"
        ),
    )
    op.create_index(
        "ix_liability_balances_liability_id_fecha", "liability_balances", ["liability_id", "fecha"]
    )
    op.create_index("ix_liability_balances_user_id", "liability_balances", ["user_id"])

    # --- Foto mensual ------------------------------------------------------
    op.create_table(
        "net_worth_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("total_activos", MONEY, nullable=False),
        sa.Column("total_pasivos", MONEY, nullable=False),
        sa.Column("patrimonio_neto", MONEY, nullable=False),
        sa.Column("moneda_base", currency, nullable=False),
        sa.Column("tasa_estimada", sa.Boolean(), nullable=False),
        sa.Column("tasa_usd", RATE, nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "patrimonio_neto = total_activos - total_pasivos",
            name=op.f("ck_net_worth_snapshots_patrimonio_cuadra"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_net_worth_snapshots_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_worth_snapshots")),
        sa.UniqueConstraint("user_id", "fecha", name="uq_net_worth_snapshots_user_id_fecha"),
    )
    op.create_index(
        "ix_net_worth_snapshots_user_id_fecha", "net_worth_snapshots", ["user_id", "fecha"]
    )


def downgrade() -> None:
    op.drop_index("ix_net_worth_snapshots_user_id_fecha", table_name="net_worth_snapshots")
    op.drop_table("net_worth_snapshots")

    op.drop_index("ix_liability_balances_user_id", table_name="liability_balances")
    op.drop_index("ix_liability_balances_liability_id_fecha", table_name="liability_balances")
    op.drop_table("liability_balances")

    op.drop_index("ix_liabilities_user_id_activa", table_name="liabilities")
    op.drop_table("liabilities")

    op.drop_index("ix_asset_valuations_user_id", table_name="asset_valuations")
    op.drop_index("ix_asset_valuations_asset_id_fecha", table_name="asset_valuations")
    op.drop_table("asset_valuations")

    op.drop_index("ix_assets_user_id_activo", table_name="assets")
    op.drop_table("assets")

    # Solo los ENUM que nacieron aqui. `currency` sigue en uso por las tablas
    # de 0002, asi que borrarlo romperia el downgrade.
    bind = op.get_bind()
    for enum in reversed(ENUMS_NUEVOS):
        enum.drop(bind, checkfirst=True)
