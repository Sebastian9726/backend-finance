"""Activos y su historial de valuaciones.

`valor_actual` NO es una columna: es el valor de la valuacion mas reciente,
resuelto en SQL. Es la misma decision que con el saldo de una cuenta -- una
columna denormalizada se desincroniza en cuanto se corrige una valuacion
pasada, y el error solo se nota meses despues, cuando la grafica historica ya
se ve rara.

Toda mutacion que pueda mover la serie de patrimonio dispara
`recompute_snapshots` **desde la fecha afectada**, no desde el principio.
"""

import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_owned_or_404, owned
from app.models import Asset, AssetValuation, User
from app.schemas.networth import (
    AssetCreate,
    AssetDetail,
    AssetOut,
    AssetUpdate,
    ValuationCreate,
    ValuationOut,
)
from app.services import networth

CERO = Decimal("0")


def _valor_actual_expr():
    """Valor de la valuacion mas reciente, como subconsulta correlacionada."""
    return (
        select(AssetValuation.valor)
        .where(AssetValuation.asset_id == Asset.id)
        .order_by(AssetValuation.fecha.desc())
        .limit(1)
        .correlate(Asset)
        .scalar_subquery()
    )


def _fecha_valor_expr():
    return (
        select(AssetValuation.fecha)
        .where(AssetValuation.asset_id == Asset.id)
        .order_by(AssetValuation.fecha.desc())
        .limit(1)
        .correlate(Asset)
        .scalar_subquery()
    )


def _a_salida(asset: Asset, valor: Decimal | None, fecha_valor: date | None) -> dict:
    valor = valor if valor is not None else CERO
    ganancia = (
        valor - asset.costo_adquisicion if asset.costo_adquisicion is not None else None
    )
    return {
        **asset.__dict__,
        "valor_actual": valor,
        "fecha_valor": fecha_valor,
        "ganancia": ganancia,
    }


async def list_assets(
    db: AsyncSession, user_id: uuid.UUID, *, solo_activos: bool = False
) -> list[AssetOut]:
    query = owned(Asset, user_id).add_columns(_valor_actual_expr(), _fecha_valor_expr())
    if solo_activos:
        query = query.where(Asset.activo.is_(True))

    result = await db.execute(query.order_by(Asset.activo.desc(), Asset.nombre))
    return [
        AssetOut.model_validate(_a_salida(asset, valor, fecha))
        for asset, valor, fecha in result.all()
    ]


async def get_asset(db: AsyncSession, user_id: uuid.UUID, asset_id: uuid.UUID) -> AssetDetail:
    result = await db.execute(
        owned(Asset, user_id)
        .add_columns(_valor_actual_expr(), _fecha_valor_expr())
        .where(Asset.id == asset_id)
    )
    fila = result.one_or_none()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activo no encontrado")

    asset, valor, fecha_valor = fila
    valuaciones = await list_valuations(db, user_id, asset_id)
    return AssetDetail.model_validate(
        {**_a_salida(asset, valor, fecha_valor), "valuaciones": valuaciones}
    )


async def create_asset(db: AsyncSession, user: User, data: AssetCreate) -> AssetDetail:
    """Crea el activo y su PRIMERA valuacion en la misma operacion.

    Un activo sin valuacion no aportaria nada a la serie de patrimonio y
    quedaria como una fila muerta que el usuario no entiende por que no aparece
    en la grafica. La fecha del valor cae, en orden, en `fecha_valor`,
    `fecha_adquisicion` u hoy.
    """
    fecha_valor = data.fecha_valor or data.fecha_adquisicion or date.today()

    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        nombre=data.nombre,
        tipo=data.tipo,
        moneda=data.moneda,
        costo_adquisicion=data.costo_adquisicion,
        fecha_adquisicion=data.fecha_adquisicion,
        notas=data.notas,
    )
    db.add(asset)
    db.add(
        AssetValuation(
            id=uuid.uuid4(),
            user_id=user.id,
            asset_id=asset.id,
            fecha=fecha_valor,
            valor=data.valor_actual,
            nota="Valor inicial",
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya tienes un activo llamado '{data.nombre}'",
        ) from exc

    await networth.recompute_snapshots(db, user, fecha_valor)
    return await get_asset(db, user.id, asset.id)


async def update_asset(
    db: AsyncSession, user: User, asset_id: uuid.UUID, data: AssetUpdate
) -> AssetDetail:
    """La moneda no se puede cambiar: las valuaciones ya registradas quedaron
    expresadas en ella y reinterpretarlas alteraria el historico."""
    asset = await get_owned_or_404(db, Asset, asset_id, user.id, detail="Activo no encontrado")
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(asset, campo, valor)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tienes otro activo con ese nombre",
        ) from exc

    # Ningun campo editable aqui entra en el calculo del patrimonio (nombre,
    # tipo, notas, costo historico), asi que no hace falta recomputar.
    return await get_asset(db, user.id, asset_id)


async def delete_asset(db: AsyncSession, user: User, asset_id: uuid.UUID) -> None:
    """Borra el activo y todas sus valuaciones, y rehace la serie completa.

    Para dejar de contar un activo que se vendio es mejor registrar una
    valuacion en 0 el dia de la venta: asi la grafica baja en el mes correcto.
    Borrar lo elimina de TODA la historia, como si nunca se hubiera tenido.
    """
    asset = await get_owned_or_404(db, Asset, asset_id, user.id, detail="Activo no encontrado")

    primera = await db.execute(
        select(AssetValuation.fecha)
        .where(AssetValuation.asset_id == asset_id)
        .order_by(AssetValuation.fecha)
        .limit(1)
    )
    desde = primera.scalar_one_or_none()

    await db.delete(asset)
    await db.commit()
    await networth.recompute_snapshots(db, user, desde)


# ---------------------------------------------------------------------------
# Valuaciones
# ---------------------------------------------------------------------------


async def list_valuations(
    db: AsyncSession, user_id: uuid.UUID, asset_id: uuid.UUID
) -> list[ValuationOut]:
    await get_owned_or_404(db, Asset, asset_id, user_id, detail="Activo no encontrado")
    result = await db.execute(
        owned(AssetValuation, user_id)
        .where(AssetValuation.asset_id == asset_id)
        .order_by(AssetValuation.fecha.desc())
    )
    return [ValuationOut.model_validate(v) for v in result.scalars().all()]


async def add_valuation(
    db: AsyncSession, user: User, asset_id: uuid.UUID, data: ValuationCreate
) -> ValuationOut:
    """Registra cuanto vale el activo en una fecha.

    Revaluar una fecha ya registrada la CORRIGE en vez de crear una segunda
    verdad para el mismo dia. Cualquiera de los dos casos mueve la serie desde
    esa fecha hacia adelante.
    """
    await get_owned_or_404(db, Asset, asset_id, user.id, detail="Activo no encontrado")

    existente = await db.execute(
        owned(AssetValuation, user.id).where(
            AssetValuation.asset_id == asset_id, AssetValuation.fecha == data.fecha
        )
    )
    valuacion = existente.scalar_one_or_none()

    if valuacion is None:
        valuacion = AssetValuation(
            id=uuid.uuid4(),
            user_id=user.id,
            asset_id=asset_id,
            fecha=data.fecha,
            valor=data.valor,
            nota=data.nota,
        )
        db.add(valuacion)
    else:
        valuacion.valor = data.valor
        valuacion.nota = data.nota

    await db.commit()
    await db.refresh(valuacion)
    await networth.recompute_snapshots(db, user, data.fecha)
    return ValuationOut.model_validate(valuacion)


async def delete_valuation(
    db: AsyncSession, user: User, asset_id: uuid.UUID, valuation_id: uuid.UUID
) -> None:
    """No se borra la ultima que queda: el activo se quedaria sin valor y
    desapareceria de la serie sin que nadie lo pidiera."""
    await get_owned_or_404(db, Asset, asset_id, user.id, detail="Activo no encontrado")
    valuacion = await get_owned_or_404(
        db, AssetValuation, valuation_id, user.id, detail="Valuacion no encontrada"
    )
    if valuacion.asset_id != asset_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Valuacion no encontrada"
        )

    cuantas = await db.execute(
        select(AssetValuation.id).where(AssetValuation.asset_id == asset_id).limit(2)
    )
    if len(cuantas.all()) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Es la unica valuacion del activo y sin ella quedaria sin valor. "
                "Registra otra antes de borrar esta, o borra el activo completo."
            ),
        )

    fecha = valuacion.fecha
    await db.delete(valuacion)
    await db.commit()
    await networth.recompute_snapshots(db, user, fecha)
