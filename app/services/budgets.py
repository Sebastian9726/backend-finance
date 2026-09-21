"""Presupuestos mensuales por categoria.

El limite se expresa en la **moneda base** y se compara contra el `monto_base`
que cada transaccion ya trae congelado. Asi un gasto en dolares entra al
presupuesto por lo que costo el dia que se hizo, y no cambia de tamaño cada vez
que se mueve el dolar.

La ejecucion se agrega EN SQL. Sumarla en Python significaria traer todas las
transacciones del mes para sumarlas de a una, y bastaria con paginar mal para
que el total mostrara solo lo que cupo en la pagina.
"""

import calendar
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_owned_or_404, owned
from app.models import Budget, Category, CategoryType, Transaction, TransactionType, User
from app.schemas.base import quantize_money
from app.schemas.planning import (
    BudgetCopyResult,
    BudgetCreate,
    BudgetLine,
    BudgetOut,
    BudgetState,
    BudgetStatus,
    BudgetUpdate,
    UnbudgetedLine,
)

CERO = Decimal("0")


def rango_del_mes(anio: int, mes: int) -> tuple[date, date]:
    """Primer y ultimo dia del mes, ambos inclusive."""
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _estado(ejecutado: Decimal, limite: Decimal, alerta_pct: Decimal) -> BudgetState:
    if ejecutado > limite:
        return BudgetState.EXCEDIDO
    # El umbral se evalua multiplicando, no dividiendo: comparar
    # `ejecutado / limite >= alerta_pct` metaria un float en la decision.
    if ejecutado * 100 >= limite * alerta_pct:
        return BudgetState.ALERTA
    return BudgetState.OK


async def list_budgets(
    db: AsyncSession, user_id: uuid.UUID, anio: int, mes: int
) -> list[BudgetOut]:
    result = await db.execute(
        owned(Budget, user_id)
        .where(Budget.anio == anio, Budget.mes == mes)
        .order_by(Budget.monto_limite.desc())
    )
    return [BudgetOut.model_validate(b) for b in result.scalars().all()]


async def create_budget(
    db: AsyncSession, user_id: uuid.UUID, data: BudgetCreate
) -> BudgetOut:
    """La categoria tiene que ser propia y de gasto: presupuestar un ingreso no
    significa nada -- no se 'limita' lo que entra."""
    categoria = await get_owned_or_404(
        db, Category, data.category_id, user_id, detail="Categoria no encontrada"
    )
    if categoria.tipo != CategoryType.GASTO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Solo se presupuestan categorias de gasto",
        )

    # El nombre se copia ANTES de intentar el commit. Despues del rollback el
    # objeto queda expirado y leer `categoria.nombre` dispararia una recarga
    # perezosa fuera del contexto async, que revienta con MissingGreenlet --
    # dentro del `except`, donde el error real quedaria enmascarado.
    nombre_categoria = categoria.nombre

    budget = Budget(id=uuid.uuid4(), user_id=user_id, **data.model_dump())
    db.add(budget)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Ya hay un presupuesto de '{nombre_categoria}' para {data.mes}/{data.anio}. "
                "Editalo en vez de crear otro."
            ),
        ) from exc
    await db.refresh(budget)
    return BudgetOut.model_validate(budget)


async def update_budget(
    db: AsyncSession, user_id: uuid.UUID, budget_id: uuid.UUID, data: BudgetUpdate
) -> BudgetOut:
    """Ni la categoria ni el mes se pueden mover: seria el mismo presupuesto
    diciendo otra cosa. Para eso se borra y se crea el que corresponde."""
    budget = await get_owned_or_404(
        db, Budget, budget_id, user_id, detail="Presupuesto no encontrado"
    )
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(budget, campo, valor)
    await db.commit()
    await db.refresh(budget)
    return BudgetOut.model_validate(budget)


async def delete_budget(db: AsyncSession, user_id: uuid.UUID, budget_id: uuid.UUID) -> None:
    budget = await get_owned_or_404(
        db, Budget, budget_id, user_id, detail="Presupuesto no encontrado"
    )
    await db.delete(budget)
    await db.commit()


async def status_del_mes(
    db: AsyncSession, user: User, anio: int, mes: int
) -> BudgetStatus:
    """Ejecutado contra limite, categoria por categoria.

    El gasto se acota al mes con `fecha >= primero AND fecha <= ultimo`. Es el
    punto donde es facil equivocarse: un `< primero_del_siguiente` mal escrito
    arrastra el primer dia del mes que viene, y el presupuesto de enero se come
    el mercado del 1 de febrero.
    """
    desde, hasta = rango_del_mes(anio, mes)

    gasto_por_categoria = await _gasto_por_categoria(db, user.id, desde, hasta)

    result = await db.execute(
        owned(Budget, user.id)
        .add_columns(Category.nombre, Category.color)
        .join(Category, Category.id == Budget.category_id)
        .where(Budget.anio == anio, Budget.mes == mes)
        .order_by(Budget.monto_limite.desc())
    )

    lineas: list[BudgetLine] = []
    total_limite = CERO
    total_ejecutado = CERO
    cubiertas: set[uuid.UUID] = set()

    for budget, nombre, color in result.all():
        ejecutado = gasto_por_categoria.get(budget.category_id, (CERO, "", None))[0]
        cubiertas.add(budget.category_id)

        total_limite += budget.monto_limite
        total_ejecutado += ejecutado

        lineas.append(
            BudgetLine(
                id=budget.id,
                category_id=budget.category_id,
                categoria=nombre,
                color=color,
                monto_limite=budget.monto_limite,
                ejecutado=quantize_money(ejecutado),
                disponible=quantize_money(budget.monto_limite - ejecutado),
                # Porcentaje de ejecucion: es para pintar una barra, no vuelve
                # a ser dinero ni se envia a ningun lado.
                porcentaje=(
                    float(ejecutado / budget.monto_limite * 100) if budget.monto_limite else 0.0
                ),
                alerta_pct=budget.alerta_pct,
                estado=_estado(ejecutado, budget.monto_limite, budget.alerta_pct),
            )
        )

    sin_presupuesto = [
        UnbudgetedLine(
            category_id=cat_id,
            categoria=nombre,
            color=color,
            ejecutado=quantize_money(monto),
        )
        for cat_id, (monto, nombre, color) in gasto_por_categoria.items()
        if cat_id not in cubiertas and monto > 0
    ]
    sin_presupuesto.sort(key=lambda linea: linea.ejecutado, reverse=True)
    total_sin_presupuesto = sum((linea.ejecutado for linea in sin_presupuesto), CERO)

    return BudgetStatus(
        moneda_base=user.moneda_base,
        anio=anio,
        mes=mes,
        desde=desde,
        hasta=hasta,
        total_limite=quantize_money(total_limite),
        total_ejecutado=quantize_money(total_ejecutado),
        total_disponible=quantize_money(total_limite - total_ejecutado),
        total_sin_presupuesto=quantize_money(total_sin_presupuesto),
        lineas=lineas,
        sin_presupuesto=sin_presupuesto,
    )


async def _gasto_por_categoria(
    db: AsyncSession, user_id: uuid.UUID, desde: date, hasta: date
) -> dict[uuid.UUID | None, tuple[Decimal, str, str | None]]:
    """Gasto del periodo por categoria, en moneda base y en POSITIVO.

    Los gastos se guardan negativos, asi que se niegan para compararlos contra
    un limite, que es positivo por naturaleza.
    """
    result = await db.execute(
        select(
            Transaction.category_id,
            func.coalesce(Category.nombre, literal("Sin categoria")).label("nombre"),
            Category.color,
            func.sum(-Transaction.monto_base).label("total"),
        )
        .join(Category, Category.id == Transaction.category_id, isouter=True)
        .where(
            Transaction.user_id == user_id,
            Transaction.tipo == TransactionType.GASTO,
            Transaction.fecha >= desde,
            Transaction.fecha <= hasta,
        )
        .group_by(Transaction.category_id, Category.nombre, Category.color)
    )
    return {
        fila.category_id: (fila.total or CERO, fila.nombre, fila.color)
        for fila in result.all()
    }


async def copiar_del_mes_anterior(
    db: AsyncSession, user_id: uuid.UUID, anio: int, mes: int
) -> BudgetCopyResult:
    """Replica los limites del mes anterior en `anio`/`mes`.

    Las categorias que ya tienen presupuesto ese mes NO se tocan: el que llama
    puede repetir la operacion sin miedo a pisar un limite que ya ajusto a
    mano, y sin necesidad de recordar si ya la habia corrido.
    """
    anio_previo, mes_previo = _mes_anterior(anio, mes)

    previos = await db.execute(
        owned(Budget, user_id).where(Budget.anio == anio_previo, Budget.mes == mes_previo)
    )
    previos = list(previos.scalars().all())

    if not previos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay presupuestos en {mes_previo}/{anio_previo} para copiar",
        )

    existentes = await db.execute(
        select(Budget.category_id).where(
            Budget.user_id == user_id, Budget.anio == anio, Budget.mes == mes
        )
    )
    ya_tienen = {fila[0] for fila in existentes.all()}

    copiados = 0
    for previo in previos:
        if previo.category_id in ya_tienen:
            continue
        db.add(
            Budget(
                id=uuid.uuid4(),
                user_id=user_id,
                category_id=previo.category_id,
                anio=anio,
                mes=mes,
                monto_limite=previo.monto_limite,
                alerta_pct=previo.alerta_pct,
            )
        )
        copiados += 1

    await db.commit()
    return BudgetCopyResult(
        anio=anio, mes=mes, copiados=copiados, omitidos=len(previos) - copiados
    )
