"""Enumeraciones del dominio.

Se mapean a tipos ENUM nativos de Postgres. Agregar un valor nuevo exige una
migracion (`ALTER TYPE ... ADD VALUE`), lo cual es deseable: evita que entren
valores no previstos por una ruta que nadie reviso.
"""

from enum import StrEnum


class Currency(StrEnum):
    COP = "COP"
    USD = "USD"


class AccountType(StrEnum):
    EFECTIVO = "efectivo"
    BANCO = "banco"
    TARJETA_CREDITO = "tarjeta_credito"
    INVERSION = "inversion"


class CategoryType(StrEnum):
    INGRESO = "ingreso"
    GASTO = "gasto"


class TransactionType(StrEnum):
    INGRESO = "ingreso"
    GASTO = "gasto"
    TRANSFERENCIA = "transferencia"


class AssetType(StrEnum):
    """Que clase de bien es. Solo agrupa para el desglose del patrimonio; no
    cambia como se calcula nada."""

    INMUEBLE = "inmueble"
    VEHICULO = "vehiculo"
    INVERSION = "inversion"
    AHORRO = "ahorro"
    OTRO = "otro"


class LiabilityType(StrEnum):
    HIPOTECA = "hipoteca"
    VEHICULO = "vehiculo"
    TARJETA = "tarjeta"
    PERSONAL = "personal"
    OTRO = "otro"
