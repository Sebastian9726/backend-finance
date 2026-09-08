"""Cuentas."""

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_owned_or_404, owned
from app.models import Account, Transaction
from app.schemas.finance import AccountCreate, AccountOut, AccountUpdate


def _saldo_actual_expr():
    """`saldo_inicial + SUM(movimientos)`, como subconsulta correlacionada.

    El saldo NO se guarda en una columna: una columna denormalizada se
    desincroniza en cuanto se edita o se borra una transaccion, y el error solo
    aparece meses despues. Como el `monto` va con signo, la suma es directa.
    """
    movimientos = (
        select(func.coalesce(func.sum(Transaction.monto), Decimal("0")))
        .where(Transaction.account_id == Account.id)
        .correlate(Account)
        .scalar_subquery()
    )
    return (Account.saldo_inicial + movimientos).label("saldo_actual")


async def list_accounts(
    db: AsyncSession, user_id: uuid.UUID, *, solo_activas: bool = False
) -> list[AccountOut]:
    query = owned(Account, user_id).add_columns(_saldo_actual_expr())
    if solo_activas:
        query = query.where(Account.activa.is_(True))

    result = await db.execute(query.order_by(Account.activa.desc(), Account.nombre))
    return [
        AccountOut.model_validate({**account.__dict__, "saldo_actual": saldo})
        for account, saldo in result.all()
    ]


async def get_account(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID) -> AccountOut:
    result = await db.execute(
        owned(Account, user_id).add_columns(_saldo_actual_expr()).where(Account.id == account_id)
    )
    fila = result.one_or_none()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    account, saldo = fila
    return AccountOut.model_validate({**account.__dict__, "saldo_actual": saldo})


async def create_account(
    db: AsyncSession, user_id: uuid.UUID, data: AccountCreate
) -> AccountOut:
    account = Account(id=uuid.uuid4(), user_id=user_id, **data.model_dump())
    db.add(account)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya tienes una cuenta llamada '{data.nombre}'",
        ) from exc
    await db.refresh(account)
    return await get_account(db, user_id, account.id)


async def update_account(
    db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID, data: AccountUpdate
) -> AccountOut:
    """La moneda no se puede cambiar: las transacciones ya registradas quedaron
    convertidas con ella y reinterpretarlas cambiaria el historico."""
    account = await get_owned_or_404(
        db, Account, account_id, user_id, detail="Cuenta no encontrada"
    )
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(account, campo, valor)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tienes otra cuenta con ese nombre",
        ) from exc
    return await get_account(db, user_id, account_id)


async def delete_account(db: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Solo se borra una cuenta sin movimientos. Con movimientos, se desactiva:
    borrarla se llevaria por delante el historico que sustenta los reportes."""
    account = await get_owned_or_404(
        db, Account, account_id, user_id, detail="Cuenta no encontrada"
    )

    tiene_movimientos = await db.execute(
        select(Transaction.id).where(Transaction.account_id == account_id).limit(1)
    )
    if tiene_movimientos.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La cuenta tiene transacciones y no se puede borrar. "
                "Desactivala si ya no la usas."
            ),
        )

    await db.delete(account)
    await db.commit()
