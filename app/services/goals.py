"""Metas de ahorro.

Lo acumulado NO es una columna: es la suma de los aportes, resuelta en SQL.
Misma decision que con el saldo de una cuenta o el valor de un activo -- una
columna denormalizada se desincroniza al corregir un aporte viejo, y aqui el
error es peor que de costumbre porque la proyeccion lo amplifica: con un
acumulado equivocado, la fecha de cumplimiento lleva meses mintiendo.

Cada meta vive en UNA moneda y sus aportes van en ella. Asi la proyeccion no
cruza tasas en ningun punto.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_owned_or_404, owned
from app.models import Account, Goal, GoalContribution
from app.schemas.base import quantize_money
from app.schemas.planning import (
    ContributionCreate,
    ContributionOut,
    GoalCreate,
    GoalDetail,
    GoalOut,
    GoalUpdate,
)

CERO = Decimal("0")
DIAS_DEL_MES = Decimal("30.44")  # promedio del año gregoriano

# Menos de esto y el "ritmo mensual" seria una extrapolacion de unos pocos
# dias: aportar 100.000 el martes no significa un ritmo de 1.500.000 al mes.
DIAS_MINIMOS_PARA_PROYECTAR = 14


def _acumulado_expr():
    """Suma de los aportes, como subconsulta correlacionada."""
    return (
        select(func.coalesce(func.sum(GoalContribution.monto), CERO))
        .where(GoalContribution.goal_id == Goal.id)
        .correlate(Goal)
        .scalar_subquery()
    )


def _primer_aporte_expr():
    return (
        select(func.min(GoalContribution.fecha))
        .where(GoalContribution.goal_id == Goal.id)
        .correlate(Goal)
        .scalar_subquery()
    )


def _a_salida(goal: Goal, acumulado: Decimal | None, primer_aporte: date | None) -> dict:
    """Calcula progreso y proyeccion a partir de lo aportado.

    La proyeccion sale del ritmo OBSERVADO -- lo aportado dividido por el tiempo
    transcurrido desde el primer aporte -- y no de un supuesto. Se omite
    (`None`) cuando no hay con que calcularla: sin historia suficiente, una
    fecha de cumplimiento seria adivinar, y una adivinanza presentada como dato
    es peor que no decir nada.
    """
    acumulado = acumulado if acumulado is not None else CERO
    objetivo = goal.monto_objetivo
    faltante = objetivo - acumulado
    cumplida = acumulado >= objetivo

    porcentaje = float(acumulado / objetivo * 100) if objetivo else 0.0
    # La barra se llena, no se desborda: el exceso ya lo dice `cumplida`.
    porcentaje = max(0.0, min(porcentaje, 100.0))

    hoy = date.today()
    promedio: Decimal | None = None
    proyectada: date | None = None

    dias = (hoy - primer_aporte).days if primer_aporte else 0
    if primer_aporte and dias >= DIAS_MINIMOS_PARA_PROYECTAR and acumulado > 0:
        meses = Decimal(dias) / DIAS_DEL_MES
        promedio = quantize_money(acumulado / meses)

        if not cumplida and promedio > 0:
            meses_faltantes = faltante / promedio
            proyectada = hoy + timedelta(days=int(meses_faltantes * DIAS_DEL_MES))

    requerido: Decimal | None = None
    if goal.fecha_objetivo and not cumplida:
        dias_restantes = (goal.fecha_objetivo - hoy).days
        if dias_restantes > 0:
            requerido = quantize_money(faltante / (Decimal(dias_restantes) / DIAS_DEL_MES))
        else:
            # La fecha ya paso y todavia falta: lo requerido es todo, ya.
            requerido = quantize_money(faltante)

    en_riesgo = bool(
        goal.fecha_objetivo and proyectada and not cumplida and proyectada > goal.fecha_objetivo
    )

    return {
        **goal.__dict__,
        "monto_actual": quantize_money(acumulado),
        "monto_faltante": quantize_money(max(faltante, CERO)),
        "porcentaje": porcentaje,
        "cumplida": cumplida,
        "aporte_mensual_promedio": promedio,
        "fecha_proyectada": proyectada,
        "aporte_mensual_requerido": requerido,
        "en_riesgo": en_riesgo,
    }


async def list_goals(
    db: AsyncSession, user_id: uuid.UUID, *, solo_activas: bool = False
) -> list[GoalOut]:
    query = owned(Goal, user_id).add_columns(_acumulado_expr(), _primer_aporte_expr())
    if solo_activas:
        query = query.where(Goal.activa.is_(True))

    result = await db.execute(query.order_by(Goal.activa.desc(), Goal.nombre))
    return [
        GoalOut.model_validate(_a_salida(goal, acumulado, primero))
        for goal, acumulado, primero in result.all()
    ]


async def get_goal(db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> GoalDetail:
    result = await db.execute(
        owned(Goal, user_id)
        .add_columns(_acumulado_expr(), _primer_aporte_expr())
        .where(Goal.id == goal_id)
    )
    fila = result.one_or_none()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meta no encontrada")

    goal, acumulado, primero = fila
    aportes = await list_contributions(db, user_id, goal_id)
    return GoalDetail.model_validate({**_a_salida(goal, acumulado, primero), "aportes": aportes})


async def create_goal(db: AsyncSession, user_id: uuid.UUID, data: GoalCreate) -> GoalDetail:
    if data.account_id is not None:
        await get_owned_or_404(
            db, Account, data.account_id, user_id, detail="Cuenta no encontrada"
        )

    goal = Goal(
        id=uuid.uuid4(),
        user_id=user_id,
        nombre=data.nombre,
        moneda=data.moneda,
        monto_objetivo=data.monto_objetivo,
        fecha_objetivo=data.fecha_objetivo,
        account_id=data.account_id,
        notas=data.notas,
    )
    db.add(goal)

    # Lo ya ahorrado entra como primer aporte y no como columna, para que la
    # meta arranque con historia y el ritmo se pueda medir desde el principio.
    if data.monto_inicial and data.monto_inicial > 0:
        db.add(
            GoalContribution(
                id=uuid.uuid4(),
                user_id=user_id,
                goal_id=goal.id,
                fecha=date.today(),
                monto=data.monto_inicial,
                nota="Ahorro inicial",
            )
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya tienes una meta llamada '{data.nombre}'",
        ) from exc

    return await get_goal(db, user_id, goal.id)


async def update_goal(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, data: GoalUpdate
) -> GoalDetail:
    """La moneda no se cambia: los aportes ya registrados estan en ella."""
    goal = await get_owned_or_404(db, Goal, goal_id, user_id, detail="Meta no encontrada")

    campos = data.model_dump(exclude_unset=True)
    if campos.get("account_id") is not None:
        await get_owned_or_404(
            db, Account, campos["account_id"], user_id, detail="Cuenta no encontrada"
        )

    for campo, valor in campos.items():
        setattr(goal, campo, valor)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tienes otra meta con ese nombre",
        ) from exc
    return await get_goal(db, user_id, goal_id)


async def delete_goal(db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> None:
    goal = await get_owned_or_404(db, Goal, goal_id, user_id, detail="Meta no encontrada")
    await db.delete(goal)
    await db.commit()


# ---------------------------------------------------------------------------
# Aportes
# ---------------------------------------------------------------------------


async def list_contributions(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID
) -> list[ContributionOut]:
    await get_owned_or_404(db, Goal, goal_id, user_id, detail="Meta no encontrada")
    result = await db.execute(
        owned(GoalContribution, user_id)
        .where(GoalContribution.goal_id == goal_id)
        .order_by(GoalContribution.fecha.desc(), GoalContribution.created_at.desc())
    )
    return [ContributionOut.model_validate(a) for a in result.scalars().all()]


async def add_contribution(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, data: ContributionCreate
) -> ContributionOut:
    """Registra un abono (o un retiro, si el monto es negativo).

    A diferencia de las valuaciones de un activo, aqui varios aportes el mismo
    dia se SUMAN en vez de reemplazarse: son eventos, no fotos del estado.
    """
    await get_owned_or_404(db, Goal, goal_id, user_id, detail="Meta no encontrada")

    if data.monto == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El aporte no puede ser cero",
        )

    aporte = GoalContribution(
        id=uuid.uuid4(),
        user_id=user_id,
        goal_id=goal_id,
        fecha=data.fecha,
        monto=data.monto,
        nota=data.nota,
    )
    db.add(aporte)
    await db.commit()
    await db.refresh(aporte)
    return ContributionOut.model_validate(aporte)


async def delete_contribution(
    db: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, contribution_id: uuid.UUID
) -> None:
    await get_owned_or_404(db, Goal, goal_id, user_id, detail="Meta no encontrada")
    aporte = await get_owned_or_404(
        db, GoalContribution, contribution_id, user_id, detail="Aporte no encontrado"
    )
    if aporte.goal_id != goal_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aporte no encontrado")

    await db.delete(aporte)
    await db.commit()
