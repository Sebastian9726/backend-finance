"""Schemas del nucleo transaccional."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.models.enums import AccountType, CategoryType, Currency, TransactionType
from app.schemas.base import Money, Rate, Schema

# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------


class AccountCreate(Schema):
    nombre: str = Field(min_length=1, max_length=120)
    tipo: AccountType
    moneda: Currency
    saldo_inicial: Decimal = Decimal("0")


class AccountUpdate(Schema):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    tipo: AccountType | None = None
    saldo_inicial: Decimal | None = None
    activa: bool | None = None


class AccountOut(Schema):
    id: uuid.UUID
    nombre: str
    tipo: AccountType
    moneda: Currency
    saldo_inicial: Money
    activa: bool
    # Calculado en SQL: saldo_inicial + SUM(monto). No es una columna.
    saldo_actual: Money


# ---------------------------------------------------------------------------
# Categorias
# ---------------------------------------------------------------------------


class CategoryCreate(Schema):
    nombre: str = Field(min_length=1, max_length=120)
    tipo: CategoryType
    parent_id: uuid.UUID | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    icono: str | None = Field(default=None, max_length=60)


class CategoryUpdate(Schema):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    icono: str | None = Field(default=None, max_length=60)


class CategoryOut(Schema):
    id: uuid.UUID
    nombre: str
    tipo: CategoryType
    parent_id: uuid.UUID | None
    color: str | None
    icono: str | None


# ---------------------------------------------------------------------------
# Transacciones
# ---------------------------------------------------------------------------


def _validar_signo(monto: Decimal, tipo: TransactionType) -> None:
    """Refleja el CHECK de la base de datos para que el error sea un 422 claro
    y no un fallo de integridad de Postgres."""
    if tipo is TransactionType.INGRESO and monto <= 0:
        raise ValueError("Un ingreso debe tener monto positivo")
    if tipo is TransactionType.GASTO and monto >= 0:
        raise ValueError("Un gasto debe tener monto negativo")
    if monto == 0:
        raise ValueError("El monto no puede ser cero")


class TransactionCreate(Schema):
    """El `monto` va CON SIGNO, igual que se guarda y que se devuelve: positivo
    en un ingreso, negativo en un gasto. Mantener el mismo contrato al escribir
    y al leer evita una capa de traduccion donde se esconden errores de signo.

    Las transferencias no se crean aqui, sino en POST /transactions/transfer.
    """

    account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    tipo: TransactionType
    monto: Decimal
    fecha: date
    descripcion: str = Field(min_length=1, max_length=255)
    notas: str | None = None

    @model_validator(mode="after")
    def signo_coherente(self):
        if self.tipo is TransactionType.TRANSFERENCIA:
            raise ValueError("Las transferencias se crean en POST /transactions/transfer")
        _validar_signo(self.monto, self.tipo)
        return self


class TransactionUpdate(Schema):
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    monto: Decimal | None = None
    fecha: date | None = None
    descripcion: str | None = Field(default=None, min_length=1, max_length=255)
    notas: str | None = None


class TransferCreate(Schema):
    """Mover dinero entre dos cuentas propias.

    Genera DOS transacciones ligadas por `transfer_group_id`: una negativa en
    la cuenta de origen y otra positiva en la de destino.
    """

    cuenta_origen_id: uuid.UUID
    cuenta_destino_id: uuid.UUID
    monto: Decimal = Field(gt=0, description="Siempre positivo: el signo lo pone cada pata")
    fecha: date
    descripcion: str = Field(min_length=1, max_length=255)
    notas: str | None = None

    @model_validator(mode="after")
    def cuentas_distintas(self):
        if self.cuenta_origen_id == self.cuenta_destino_id:
            raise ValueError("La cuenta de origen y la de destino deben ser distintas")
        return self


class CategoryRef(Schema):
    id: uuid.UUID
    nombre: str
    color: str | None
    icono: str | None


class AccountRef(Schema):
    id: uuid.UUID
    nombre: str
    moneda: Currency


class TransactionOut(Schema):
    id: uuid.UUID
    tipo: TransactionType
    monto: Money
    moneda: Currency
    tasa_a_base: Rate
    monto_base: Money
    fecha: date
    descripcion: str
    notas: str | None
    transfer_group_id: uuid.UUID | None
    account: AccountRef
    category: CategoryRef | None


# ---------------------------------------------------------------------------
# Tasas de cambio
# ---------------------------------------------------------------------------


class ExchangeRateCreate(Schema):
    fecha: date
    origen: Currency
    destino: Currency
    tasa: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def monedas_distintas(self):
        if self.origen == self.destino:
            raise ValueError("El origen y el destino deben ser monedas distintas")
        return self


class ExchangeRateOut(Schema):
    id: uuid.UUID
    fecha: date
    origen: Currency
    destino: Currency
    tasa: Rate


class RateLookup(Schema):
    """Resultado de buscar la tasa aplicable a una fecha.

    `estimada` avisa que no habia tasa para ese dia y se uso la anterior mas
    cercana. La UI lo advierte en vez de presentar el numero como exacto.
    """

    origen: Currency
    destino: Currency
    fecha_solicitada: date
    fecha_aplicada: date | None
    tasa: Rate
    estimada: bool
