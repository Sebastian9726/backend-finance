"""Reportes.

Todos los agregados se calculan EN SQL. Traer las transacciones y sumarlas en
Python seria mas lento y, sobre todo, mas fragil: `SUM` sobre `NUMERIC` es
exacto y no depende de que nadie olvide convertir a `Decimal` por el camino.

Las transferencias se excluyen siempre: mover plata de una cuenta a otra no es
ni ingreso ni gasto, y contarlas inflaria ambos lados del reporte.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Category, CategoryType, Transaction, TransactionType, User
from app.schemas.base import quantize_money
from app.schemas.reports import (
    CashflowPoint,
    CategoryBreakdown,
    CategorySlice,
    DashboardSummary,
)
from app.services import exchange_rates

CERO = Decimal("0")

_NO_TRANSFERENCIA = Transaction.tipo != TransactionType.TRANSFERENCIA

# Ingresos y gastos como sumas condicionales. `-monto_base` en los gastos
# porque el monto se guarda con signo y el reporte los muestra en positivo.
_INGRESOS = func.coalesce(
    func.sum(case((Transaction.tipo == TransactionType.INGRESO, Transaction.monto_base), else_=0)),
    CERO,
)
_GASTOS = func.coalesce(
    func.sum(case((Transaction.tipo == TransactionType.GASTO, -Transaction.monto_base), else_=0)),
    CERO,
)


async def cashflow(
    db: AsyncSession, user: User, desde: date, hasta: date
) -> list[CashflowPoint]:
    """Ingresos contra gastos, mes a mes."""
    mes = func.date_trunc("month", Transaction.fecha).label("mes")

    result = await db.execute(
        select(mes, _INGRESOS.label("ingresos"), _GASTOS.label("gastos"))
        .where(
            Transaction.user_id == user.id,
            Transaction.fecha >= desde,
            Transaction.fecha <= hasta,
            _NO_TRANSFERENCIA,
        )
        .group_by(mes)
        .order_by(mes)
    )

    return [
        CashflowPoint(
            mes=fila.mes.date() if hasattr(fila.mes, "date") else fila.mes,
            ingresos=quantize_money(fila.ingresos),
            gastos=quantize_money(fila.gastos),
            neto=quantize_money(fila.ingresos - fila.gastos),
        )
        for fila in result.all()
    ]


async def by_category(
    db: AsyncSession, user: User, tipo: CategoryType, desde: date, hasta: date
) -> CategoryBreakdown:
    """Desglose por categoria. Las transacciones sin categoria se agrupan en
    'Sin categoria' en vez de desaparecer del reporte."""
    tx_tipo = (
        TransactionType.INGRESO if tipo is CategoryType.INGRESO else TransactionType.GASTO
    )
    # Los gastos se guardan negativos; el reporte los presenta en positivo.
    es_ingreso = tx_tipo is TransactionType.INGRESO
    monto = Transaction.monto_base if es_ingreso else -Transaction.monto_base

    result = await db.execute(
        select(
            Transaction.category_id,
            func.coalesce(Category.nombre, literal("Sin categoria")).label("nombre"),
            Category.color,
            func.sum(monto).label("total"),
        )
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .where(
            Transaction.user_id == user.id,
            Transaction.tipo == tx_tipo,
            Transaction.fecha >= desde,
            Transaction.fecha <= hasta,
        )
        .group_by(Transaction.category_id, Category.nombre, Category.color)
        .order_by(func.sum(monto).desc())
    )
    filas = result.all()
    total = sum((fila.total for fila in filas), CERO)

    return CategoryBreakdown(
        tipo=tipo,
        desde=desde,
        hasta=hasta,
        total=quantize_money(total),
        items=[
            CategorySlice(
                category_id=fila.category_id,
                nombre=fila.nombre,
                color=fila.color,
                total=quantize_money(fila.total),
                porcentaje=float(fila.total / total * 100) if total else 0.0,
            )
            for fila in filas
        ],
    )


async def summary(db: AsyncSession, user: User, desde: date, hasta: date) -> DashboardSummary:
    """Cifras del encabezado del tablero."""
    periodo = await db.execute(
        select(
            _INGRESOS.label("ingresos"),
            _GASTOS.label("gastos"),
            func.count(Transaction.id).label("transacciones"),
        ).where(
            Transaction.user_id == user.id,
            Transaction.fecha >= desde,
            Transaction.fecha <= hasta,
            _NO_TRANSFERENCIA,
        )
    )
    fila = periodo.one()

    saldo_total = await saldo_consolidado(db, user, hasta)

    return DashboardSummary(
        moneda_base=user.moneda_base,
        desde=desde,
        hasta=hasta,
        ingresos=quantize_money(fila.ingresos),
        gastos=quantize_money(fila.gastos),
        neto=quantize_money(fila.ingresos - fila.gastos),
        saldo_total=saldo_total,
        transacciones=fila.transacciones,
    )


async def saldo_consolidado(db: AsyncSession, user: User, al: date) -> Decimal:
    """Suma de los saldos de las cuentas activas, en la moneda base.

    Los saldos se agregan POR MONEDA en SQL y solo despues se convierten: sumar
    directamente pesos con dolares daria un numero sin significado. La
    conversion usa la tasa vigente a la fecha `al`, no la de cada movimiento,
    porque lo que se responde es "cuanto tengo hoy", no "cuanto valia cuando
    lo recibi".
    """
    movimientos = (
        select(func.coalesce(func.sum(Transaction.monto), CERO))
        .where(Transaction.account_id == Account.id)
        .correlate(Account)
        .scalar_subquery()
    )
    por_cuenta = (
        select(
            Account.moneda.label("moneda"),
            (Account.saldo_inicial + movimientos).label("saldo"),
        )
        .where(Account.user_id == user.id, Account.activa.is_(True))
        .subquery()
    )

    result = await db.execute(
        select(por_cuenta.c.moneda, func.sum(por_cuenta.c.saldo)).group_by(por_cuenta.c.moneda)
    )

    total = CERO
    for moneda, saldo in result.all():
        if saldo is None:
            continue
        if moneda == user.moneda_base:
            total += saldo
        else:
            lookup = await exchange_rates.get_rate(db, moneda, user.moneda_base, al)
            total += saldo * lookup.tasa

    return quantize_money(total)
