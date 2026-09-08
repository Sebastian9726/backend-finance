"""Nucleo transaccional: usuarios, cuentas, categorias y transacciones

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-08

Nota sobre los ENUM: el autogenerate los declara dentro de cada `create_table`,
lo que emite un `CREATE TYPE` por tabla y falla en la segunda que usa
`currency`. Ademas el `downgrade` generado no los borra, asi que
`downgrade -> upgrade` fallaria con "type already exists". Aqui se crean una
sola vez al principio (`create_type=False` en las columnas) y se borran al
final del downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


currency = postgresql.ENUM("COP", "USD", name="currency", create_type=False)
account_type = postgresql.ENUM(
    "efectivo", "banco", "tarjeta_credito", "inversion", name="account_type", create_type=False
)
category_type = postgresql.ENUM("ingreso", "gasto", name="category_type", create_type=False)
transaction_type = postgresql.ENUM(
    "ingreso", "gasto", "transferencia", name="transaction_type", create_type=False
)

ENUMS = (currency, account_type, category_type, transaction_type)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("moneda_base", currency, nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"), "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_refresh_tokens_user_id_family_id", "refresh_tokens", ["user_id", "family_id"]
    )

    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("origen", currency, nullable=False),
        sa.Column("destino", currency, nullable=False),
        sa.Column("tasa", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("tasa > 0", name=op.f("ck_exchange_rates_tasa_positiva")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange_rates")),
        sa.UniqueConstraint(
            "fecha", "origen", "destino", name="uq_exchange_rates_fecha_origen_destino"
        ),
    )
    op.create_index(
        "ix_exchange_rates_origen_destino_fecha",
        "exchange_rates",
        ["origen", "destino", "fecha"],
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo", account_type, nullable=False),
        sa.Column("moneda", currency, nullable=False),
        sa.Column("saldo_inicial", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_accounts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("user_id", "nombre", name="uq_accounts_user_id_nombre"),
    )
    op.create_index("ix_accounts_user_id_activa", "accounts", ["user_id", "activa"])

    op.create_table(
        "categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("tipo", category_type, nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column("icono", sa.String(length=60), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name=op.f("fk_categories_parent_id_categories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_categories_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("user_id", "nombre", "tipo", name="uq_categories_user_id_nombre_tipo"),
    )
    op.create_index("ix_categories_user_id_tipo", "categories", ["user_id", "tipo"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("tipo", transaction_type, nullable=False),
        sa.Column("monto", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("moneda", currency, nullable=False),
        sa.Column("tasa_a_base", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("monto_base", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("descripcion", sa.String(length=255), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("transfer_group_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # El signo del monto es el efecto sobre el saldo. Estos CHECK impiden
        # que entre un gasto positivo o un ingreso negativo, que serian
        # invisibles hasta que un reporte diera raro.
        sa.CheckConstraint(
            "(tipo = 'ingreso' AND monto > 0) "
            "OR (tipo = 'gasto' AND monto < 0) "
            "OR (tipo = 'transferencia' AND monto <> 0)",
            name=op.f("ck_transactions_monto_signo_coherente_con_tipo"),
        ),
        sa.CheckConstraint(
            "(monto >= 0) = (monto_base >= 0)",
            name=op.f("ck_transactions_monto_base_mismo_signo"),
        ),
        sa.CheckConstraint("tasa_a_base > 0", name=op.f("ck_transactions_tasa_positiva")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_transactions_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_transactions_category_id_categories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_transactions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index("ix_transactions_user_id_fecha", "transactions", ["user_id", "fecha"])
    op.create_index("ix_transactions_user_id_account_id", "transactions", ["user_id", "account_id"])
    op.create_index(
        "ix_transactions_user_id_category_id", "transactions", ["user_id", "category_id"]
    )
    op.create_index("ix_transactions_transfer_group_id", "transactions", ["transfer_group_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_transfer_group_id", table_name="transactions")
    op.drop_index("ix_transactions_user_id_category_id", table_name="transactions")
    op.drop_index("ix_transactions_user_id_account_id", table_name="transactions")
    op.drop_index("ix_transactions_user_id_fecha", table_name="transactions")
    op.drop_table("transactions")

    op.drop_index("ix_categories_user_id_tipo", table_name="categories")
    op.drop_table("categories")

    op.drop_index("ix_accounts_user_id_activa", table_name="accounts")
    op.drop_table("accounts")

    op.drop_index("ix_exchange_rates_origen_destino_fecha", table_name="exchange_rates")
    op.drop_table("exchange_rates")

    op.drop_index("ix_refresh_tokens_user_id_family_id", table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    # Los ENUM se borran de ultimo: mientras exista una columna que los use,
    # Postgres rechaza el DROP TYPE.
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
