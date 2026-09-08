"""Transacciones y transferencias."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import HTTP_422
from app.core.tenant import get_owned_or_404, owned
from app.models import Account, Category, Transaction, TransactionType, User
from app.schemas.base import quantize_money
from app.schemas.finance import TransactionCreate, TransactionUpdate, TransferCreate
from app.services import exchange_rates

_CARGAR_RELACIONES = (selectinload(Transaction.account), selectinload(Transaction.category))


async def _convertir_a_base(
    db: AsyncSession, user: User, monto: Decimal, moneda, fecha: date
) -> tuple[Decimal, Decimal]:
    """Devuelve (tasa_a_base, monto_base) para la fecha del movimiento.

    La tasa queda congelada en la fila. Los reportes suman `monto_base` y nunca
    reconvierten: si se recalculara con la tasa de hoy, el patrimonio del ano
    pasado cambiaria cada vez que se mueve el dolar.
    """
    lookup = await exchange_rates.get_rate(db, moneda, user.moneda_base, fecha)
    return lookup.tasa, quantize_money(monto * lookup.tasa)


async def _validar_categoria(
    db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID | None, tipo: TransactionType
) -> None:
    if category_id is None:
        return
    category = await get_owned_or_404(
        db, Category, category_id, user_id, detail="Categoria no encontrada"
    )
    if category.tipo.value != tipo.value:
        raise HTTPException(
            status_code=HTTP_422,
            detail=(
                f"La categoria '{category.nombre}' es de {category.tipo.value}, "
                f"no de {tipo.value}"
            ),
        )


async def list_transactions(
    db: AsyncSession,
    user: User,
    *,
    desde: date | None = None,
    hasta: date | None = None,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    tipo: TransactionType | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Transaction], int]:
    filtros = []
    if desde is not None:
        filtros.append(Transaction.fecha >= desde)
    if hasta is not None:
        filtros.append(Transaction.fecha <= hasta)
    if account_id is not None:
        filtros.append(Transaction.account_id == account_id)
    if category_id is not None:
        filtros.append(Transaction.category_id == category_id)
    if tipo is not None:
        filtros.append(Transaction.tipo == tipo)
    if q:
        patron = f"%{q.strip()}%"
        filtros.append(
            or_(Transaction.descripcion.ilike(patron), Transaction.notas.ilike(patron))
        )

    total = await db.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.user_id == user.id, *filtros)
    )

    result = await db.execute(
        owned(Transaction, user.id)
        .where(*filtros)
        .options(*_CARGAR_RELACIONES)
        # `id` como segundo criterio para que el orden sea estable: sin el, dos
        # transacciones del mismo dia pueden alternarse entre paginas.
        .order_by(Transaction.fecha.desc(), Transaction.created_at.desc(), Transaction.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total or 0


async def get_transaction(
    db: AsyncSession, user_id: uuid.UUID, transaction_id: uuid.UUID
) -> Transaction:
    result = await db.execute(
        owned(Transaction, user_id)
        .where(Transaction.id == transaction_id)
        .options(*_CARGAR_RELACIONES)
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaccion no encontrada"
        )
    return transaction


async def create_transaction(
    db: AsyncSession, user: User, data: TransactionCreate
) -> Transaction:
    account = await get_owned_or_404(
        db, Account, data.account_id, user.id, detail="Cuenta no encontrada"
    )
    await _validar_categoria(db, user.id, data.category_id, data.tipo)

    # La moneda la define la cuenta: un movimiento de una cuenta en dolares
    # esta en dolares. Aceptarla como parametro abriria la puerta a registrar
    # pesos en una cuenta en dolares.
    tasa, monto_base = await _convertir_a_base(
        db, user, data.monto, account.moneda, data.fecha
    )

    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        category_id=data.category_id,
        tipo=data.tipo,
        monto=quantize_money(data.monto),
        moneda=account.moneda,
        tasa_a_base=tasa,
        monto_base=monto_base,
        fecha=data.fecha,
        descripcion=data.descripcion,
        notas=data.notas,
    )
    db.add(transaction)
    await db.commit()
    return await get_transaction(db, user.id, transaction.id)


async def update_transaction(
    db: AsyncSession, user: User, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction:
    transaction = await get_transaction(db, user.id, transaction_id)

    if transaction.transfer_group_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Las patas de una transferencia no se editan por separado. "
                "Borra la transferencia y registrala de nuevo."
            ),
        )

    cambios = data.model_dump(exclude_unset=True)

    if "account_id" in cambios:
        account = await get_owned_or_404(
            db, Account, cambios["account_id"], user.id, detail="Cuenta no encontrada"
        )
        transaction.account_id = account.id
        transaction.moneda = account.moneda

    if "category_id" in cambios:
        await _validar_categoria(db, user.id, cambios["category_id"], transaction.tipo)
        transaction.category_id = cambios["category_id"]

    if "monto" in cambios:
        monto = quantize_money(cambios["monto"])
        if transaction.tipo is TransactionType.INGRESO and monto <= 0:
            raise HTTPException(HTTP_422, "Un ingreso debe tener monto positivo")
        if transaction.tipo is TransactionType.GASTO and monto >= 0:
            raise HTTPException(HTTP_422, "Un gasto debe tener monto negativo")
        transaction.monto = monto

    for campo in ("fecha", "descripcion", "notas"):
        if campo in cambios:
            setattr(transaction, campo, cambios[campo])

    # Cambiar el monto, la fecha o la cuenta cambia la conversion: la fecha
    # porque corresponde otra tasa, la cuenta porque puede ser otra moneda.
    if {"monto", "fecha", "account_id"} & cambios.keys():
        account_actual = await db.get(Account, transaction.account_id)
        transaction.tasa_a_base, transaction.monto_base = await _convertir_a_base(
            db, user, transaction.monto, account_actual.moneda, transaction.fecha
        )

    await db.commit()
    return await get_transaction(db, user.id, transaction_id)


async def delete_transaction(
    db: AsyncSession, user_id: uuid.UUID, transaction_id: uuid.UUID
) -> None:
    """Borra la transaccion. Si es una pata de transferencia, se lleva la otra:
    dejar media transferencia descuadraria los saldos de las dos cuentas."""
    transaction = await get_transaction(db, user_id, transaction_id)

    if transaction.transfer_group_id is not None:
        patas = await db.execute(
            owned(Transaction, user_id).where(
                Transaction.transfer_group_id == transaction.transfer_group_id
            )
        )
        for pata in patas.scalars().all():
            await db.delete(pata)
    else:
        await db.delete(transaction)

    await db.commit()


async def create_transfer(
    db: AsyncSession, user: User, data: TransferCreate
) -> list[Transaction]:
    """Mueve dinero entre dos cuentas propias.

    El `monto` se expresa en la moneda de la cuenta de ORIGEN. Si el destino
    tiene otra moneda, la pata de destino se convierte con la tasa del dia, de
    modo que cada cuenta queda con un movimiento en su propia moneda.
    """
    origen = await get_owned_or_404(
        db, Account, data.cuenta_origen_id, user.id, detail="Cuenta de origen no encontrada"
    )
    destino = await get_owned_or_404(
        db, Account, data.cuenta_destino_id, user.id, detail="Cuenta de destino no encontrada"
    )

    monto = quantize_money(data.monto)
    grupo = uuid.uuid4()

    if origen.moneda == destino.moneda:
        monto_destino = monto
    else:
        cambio = await exchange_rates.get_rate(db, origen.moneda, destino.moneda, data.fecha)
        monto_destino = quantize_money(monto * cambio.tasa)

    tasa_salida, base_salida = await _convertir_a_base(
        db, user, -monto, origen.moneda, data.fecha
    )
    tasa_entrada, base_entrada = await _convertir_a_base(
        db, user, monto_destino, destino.moneda, data.fecha
    )

    patas = [
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            account_id=origen.id,
            category_id=None,
            tipo=TransactionType.TRANSFERENCIA,
            monto=-monto,
            moneda=origen.moneda,
            tasa_a_base=tasa_salida,
            monto_base=base_salida,
            fecha=data.fecha,
            descripcion=data.descripcion,
            notas=data.notas,
            transfer_group_id=grupo,
        ),
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            account_id=destino.id,
            category_id=None,
            tipo=TransactionType.TRANSFERENCIA,
            monto=monto_destino,
            moneda=destino.moneda,
            tasa_a_base=tasa_entrada,
            monto_base=base_entrada,
            fecha=data.fecha,
            descripcion=data.descripcion,
            notas=data.notas,
            transfer_group_id=grupo,
        ),
    ]
    db.add_all(patas)
    await db.commit()

    result = await db.execute(
        owned(Transaction, user.id)
        .where(Transaction.transfer_group_id == grupo)
        .options(*_CARGAR_RELACIONES)
        .order_by(Transaction.monto)
    )
    return list(result.scalars().all())
