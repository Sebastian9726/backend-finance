"""Tipos y base comunes de los schemas."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

CENTS = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    """Redondea a dos decimales con half-up EXPLICITO.

    El default de Python es ROUND_HALF_EVEN ("banquero"), que redondea 0.005 a
    0.00 y 0.015 a 0.02. Correcto estadisticamente, pero no es lo que espera
    quien revisa una factura.
    """
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _serialize_money(value: Decimal) -> str:
    return str(quantize_money(value))


def _serialize_rate(value: Decimal) -> str:
    return str(value)


# Los montos salen del API como STRING, nunca como numero JSON: `JSON.parse`
# convierte todo numero a double y ahi se pierden centavos antes de que el
# frontend pueda hacer algo al respecto.
Money = Annotated[Decimal, PlainSerializer(_serialize_money, return_type=str, when_used="json")]

# Las tasas llevan 8 decimales y por la misma razon viajan como string.
Rate = Annotated[Decimal, PlainSerializer(_serialize_rate, return_type=str, when_used="json")]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](Schema):
    """Pagina de resultados."""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0
