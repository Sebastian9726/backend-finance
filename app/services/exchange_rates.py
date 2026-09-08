"""Tasas de cambio."""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Currency, ExchangeRate
from app.schemas.finance import ExchangeRateCreate, RateLookup

UNO = Decimal("1")


async def get_rate(
    db: AsyncSession,
    origen: Currency,
    destino: Currency,
    fecha: date,
) -> RateLookup:
    """Tasa aplicable para convertir `origen` a `destino` en una fecha.

    Busca la tasa mas reciente con fecha <= la pedida, para no exigir que el
    calendario este completo. Si la que encuentra es de un dia anterior, marca
    `estimada=True` y la UI lo advierte en vez de presentarla como exacta.

    Tambien acepta la tasa inversa: si existe USD->COP, se puede convertir
    COP->USD dividiendo.
    """
    if origen == destino:
        return RateLookup(
            origen=origen,
            destino=destino,
            fecha_solicitada=fecha,
            fecha_aplicada=fecha,
            tasa=UNO,
            estimada=False,
        )

    directa = await _buscar(db, origen, destino, fecha)
    if directa is not None:
        return RateLookup(
            origen=origen,
            destino=destino,
            fecha_solicitada=fecha,
            fecha_aplicada=directa.fecha,
            tasa=directa.tasa,
            estimada=directa.fecha != fecha,
        )

    inversa = await _buscar(db, destino, origen, fecha)
    if inversa is not None:
        return RateLookup(
            origen=origen,
            destino=destino,
            fecha_solicitada=fecha,
            fecha_aplicada=inversa.fecha,
            # 12 decimales: la inversa de una tasa grande (1 / 4200) necesita
            # mas precision que los 8 con que se guardan las tasas directas.
            tasa=(UNO / inversa.tasa).quantize(Decimal("0.000000000001")),
            estimada=inversa.fecha != fecha,
        )

    # Inventar una tasa (por ejemplo 1) contaminaria el historico en silencio.
    # Mejor fallar con un mensaje que diga exactamente que hace falta.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"No hay tasa de cambio registrada para {origen} -> {destino} "
            f"al {fecha.isoformat()}. Registrala en POST /api/v1/exchange-rates."
        ),
    )


async def _buscar(
    db: AsyncSession, origen: Currency, destino: Currency, fecha: date
) -> ExchangeRate | None:
    result = await db.execute(
        select(ExchangeRate)
        .where(
            ExchangeRate.origen == origen,
            ExchangeRate.destino == destino,
            ExchangeRate.fecha <= fecha,
        )
        .order_by(ExchangeRate.fecha.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_rate(db: AsyncSession, data: ExchangeRateCreate) -> ExchangeRate:
    """Crea o actualiza la tasa de un dia. Las tasas son globales, no por
    usuario: el dolar vale lo mismo para todo el mundo."""
    result = await db.execute(
        select(ExchangeRate).where(
            ExchangeRate.fecha == data.fecha,
            ExchangeRate.origen == data.origen,
            ExchangeRate.destino == data.destino,
        )
    )
    existente = result.scalar_one_or_none()

    if existente is not None:
        existente.tasa = data.tasa
        await db.commit()
        await db.refresh(existente)
        return existente

    rate = ExchangeRate(
        id=uuid.uuid4(),
        fecha=data.fecha,
        origen=data.origen,
        destino=data.destino,
        tasa=data.tasa,
    )
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return rate


async def list_rates(db: AsyncSession, limit: int = 100) -> list[ExchangeRate]:
    result = await db.execute(
        select(ExchangeRate).order_by(ExchangeRate.fecha.desc()).limit(limit)
    )
    return list(result.scalars().all())
