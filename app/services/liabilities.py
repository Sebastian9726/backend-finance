"""Deudas y su historial de saldos.

Espejo de `assets.py`: el saldo vigente no es una columna sino el ultimo saldo
registrado. Los saldos se guardan POSITIVOS -- son lo que se debe -- y el signo
lo pone la consolidacion del patrimonio, que resta los pasivos. Guardarlos
negativos obligaria a recordar el signo en cada consulta.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_owned_or_404, owned
from app.models import Liability, LiabilityBalance, User
from app.schemas.networth import (
    BalanceCreate,
    BalanceOut,
    LiabilityCreate,
    LiabilityDetail,
    LiabilityOut,
    LiabilityUpdate,
)
from app.services import networth

CERO = Decimal("0")


def _saldo_actual_expr():
    return (
        select(LiabilityBalance.saldo)
        .where(LiabilityBalance.liability_id == Liability.id)
        .order_by(LiabilityBalance.fecha.desc())
        .limit(1)
        .correlate(Liability)
        .scalar_subquery()
    )


def _fecha_saldo_expr():
    return (
        select(LiabilityBalance.fecha)
        .where(LiabilityBalance.liability_id == Liability.id)
        .order_by(LiabilityBalance.fecha.desc())
        .limit(1)
        .correlate(Liability)
        .scalar_subquery()
    )


def _a_salida(liability: Liability, saldo: Decimal | None, fecha_saldo: date | None) -> dict:
    saldo = saldo if saldo is not None else CERO
    abonado = liability.principal - saldo if liability.principal is not None else None
    return {
        **liability.__dict__,
        "saldo_actual": saldo,
        "fecha_saldo": fecha_saldo,
        "abonado": abonado,
    }


async def list_liabilities(
    db: AsyncSession, user_id: uuid.UUID, *, solo_activas: bool = False
) -> list[LiabilityOut]:
    query = owned(Liability, user_id).add_columns(_saldo_actual_expr(), _fecha_saldo_expr())
    if solo_activas:
        query = query.where(Liability.activa.is_(True))

    result = await db.execute(query.order_by(Liability.activa.desc(), Liability.nombre))
    return [
        LiabilityOut.model_validate(_a_salida(liability, saldo, fecha))
        for liability, saldo, fecha in result.all()
    ]


async def get_liability(
    db: AsyncSession, user_id: uuid.UUID, liability_id: uuid.UUID
) -> LiabilityDetail:
    result = await db.execute(
        owned(Liability, user_id)
        .add_columns(_saldo_actual_expr(), _fecha_saldo_expr())
        .where(Liability.id == liability_id)
    )
    fila = result.one_or_none()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deuda no encontrada")

    liability, saldo, fecha_saldo = fila
    saldos = await list_balances(db, user_id, liability_id)
    return LiabilityDetail.model_validate(
        {**_a_salida(liability, saldo, fecha_saldo), "saldos": saldos}
    )


async def create_liability(
    db: AsyncSession, user: User, data: LiabilityCreate
) -> LiabilityDetail:
    """Crea la deuda y su primer saldo. Sin saldo no pesaria en el patrimonio."""
    fecha_saldo = data.fecha_saldo or data.fecha_inicio or date.today()

    liability = Liability(
        id=uuid.uuid4(),
        user_id=user.id,
        nombre=data.nombre,
        tipo=data.tipo,
        moneda=data.moneda,
        principal=data.principal,
        tasa_interes=data.tasa_interes,
        cuota_mensual=data.cuota_mensual,
        fecha_inicio=data.fecha_inicio,
        fecha_fin=data.fecha_fin,
        notas=data.notas,
    )
    db.add(liability)
    db.add(
        LiabilityBalance(
            id=uuid.uuid4(),
            user_id=user.id,
            liability_id=liability.id,
            fecha=fecha_saldo,
            saldo=data.saldo_actual,
            nota="Saldo inicial",
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya tienes una deuda llamada '{data.nombre}'",
        ) from exc

    await networth.recompute_snapshots(db, user, fecha_saldo)
    return await get_liability(db, user.id, liability.id)


async def update_liability(
    db: AsyncSession, user: User, liability_id: uuid.UUID, data: LiabilityUpdate
) -> LiabilityDetail:
    liability = await get_owned_or_404(
        db, Liability, liability_id, user.id, detail="Deuda no encontrada"
    )
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(liability, campo, valor)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tienes otra deuda con ese nombre",
        ) from exc

    # Igual que en activos: nada de lo editable aqui entra en el calculo del
    # patrimonio, que solo mira los saldos.
    return await get_liability(db, user.id, liability_id)


async def delete_liability(db: AsyncSession, user: User, liability_id: uuid.UUID) -> None:
    """Para una deuda ya pagada es mejor registrar un saldo de 0: asi la
    grafica la ve extinguirse en el mes correcto. Borrarla la saca de toda la
    historia, como si nunca hubiera existido."""
    liability = await get_owned_or_404(
        db, Liability, liability_id, user.id, detail="Deuda no encontrada"
    )

    primera = await db.execute(
        select(LiabilityBalance.fecha)
        .where(LiabilityBalance.liability_id == liability_id)
        .order_by(LiabilityBalance.fecha)
        .limit(1)
    )
    desde = primera.scalar_one_or_none()

    await db.delete(liability)
    await db.commit()
    await networth.recompute_snapshots(db, user, desde)


# ---------------------------------------------------------------------------
# Saldos
# ---------------------------------------------------------------------------


async def list_balances(
    db: AsyncSession, user_id: uuid.UUID, liability_id: uuid.UUID
) -> list[BalanceOut]:
    await get_owned_or_404(db, Liability, liability_id, user_id, detail="Deuda no encontrada")
    result = await db.execute(
        owned(LiabilityBalance, user_id)
        .where(LiabilityBalance.liability_id == liability_id)
        .order_by(LiabilityBalance.fecha.desc())
    )
    return [BalanceOut.model_validate(b) for b in result.scalars().all()]


async def add_balance(
    db: AsyncSession, user: User, liability_id: uuid.UUID, data: BalanceCreate
) -> BalanceOut:
    """Registra cuanto se debe en una fecha. Repetir la fecha la corrige."""
    await get_owned_or_404(db, Liability, liability_id, user.id, detail="Deuda no encontrada")

    existente = await db.execute(
        owned(LiabilityBalance, user.id).where(
            LiabilityBalance.liability_id == liability_id,
            LiabilityBalance.fecha == data.fecha,
        )
    )
    balance = existente.scalar_one_or_none()

    if balance is None:
        balance = LiabilityBalance(
            id=uuid.uuid4(),
            user_id=user.id,
            liability_id=liability_id,
            fecha=data.fecha,
            saldo=data.saldo,
            nota=data.nota,
        )
        db.add(balance)
    else:
        balance.saldo = data.saldo
        balance.nota = data.nota

    await db.commit()
    await db.refresh(balance)
    await networth.recompute_snapshots(db, user, data.fecha)
    return BalanceOut.model_validate(balance)


async def delete_balance(
    db: AsyncSession, user: User, liability_id: uuid.UUID, balance_id: uuid.UUID
) -> None:
    await get_owned_or_404(db, Liability, liability_id, user.id, detail="Deuda no encontrada")
    balance = await get_owned_or_404(
        db, LiabilityBalance, balance_id, user.id, detail="Saldo no encontrado"
    )
    if balance.liability_id != liability_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saldo no encontrado")

    cuantos = await db.execute(
        select(LiabilityBalance.id)
        .where(LiabilityBalance.liability_id == liability_id)
        .limit(2)
    )
    if len(cuantos.all()) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Es el unico saldo de la deuda y sin el quedaria sin valor. "
                "Registra otro antes de borrar este, o borra la deuda completa."
            ),
        )

    fecha = balance.fecha
    await db.delete(balance)
    await db.commit()
    await networth.recompute_snapshots(db, user, fecha)
