"""Patrimonio neto en el tiempo.

La serie se **materializa** en `net_worth_snapshots`: una fila por cierre de
mes. Es la unica cifra derivada que el proyecto guarda en vez de calcular al
vuelo, porque reconstruirla exige, por cada mes y cada activo o deuda, buscar
su ultima valuacion y la tasa de cambio de esa fecha. Se recalcula **desde la
fecha afectada hacia adelante**, nunca toda la historia.

Dos reglas que hacen que la grafica no mienta:

- **Cada mes usa la tasa de cambio de su propio cierre.** Un apartamento en USD
  en marzo de 2025 vale, en pesos, lo que valia con el dolar de marzo de 2025.
  Reconvertir con la tasa de hoy haria que el patrimonio del año pasado
  cambiara cada vez que se mueve el dolar.
- **El flag `activo` no se aplica hacia atras.** Un carro vendido en junio
  seguia siendo patrimonio en mayo. Vender se registra poniendo una valuacion
  en 0 el dia de la venta (y desactivando el activo, que es solo cosmetico);
  asi la serie baja en el mes correcto en vez de reescribir el pasado.
"""

import calendar
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Asset,
    AssetValuation,
    Currency,
    ExchangeRate,
    Liability,
    LiabilityBalance,
    NetWorthSnapshot,
    User,
)
from app.schemas.base import quantize_money
from app.schemas.networth import (
    CompositionItem,
    NetWorthComposition,
    NetWorthPoint,
    NetWorthSeries,
    RecomputeResult,
)
from app.services import exchange_rates

CERO = Decimal("0")
UNO = Decimal("1")

# Tope de seguridad: recomputar mas de 50 años de cierres mensuales solo puede
# venir de una fecha absurda escrita por error.
MAX_MESES = 600


def fin_de_mes(d: date) -> date:
    """Ultimo dia del mes de `d`. Es la fecha canonica de todo snapshot."""
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _mes_siguiente(d: date) -> date:
    return fin_de_mes(date(d.year + (d.month == 12), d.month % 12 + 1, 1))


def _meses_entre(desde: date, hasta: date) -> list[date]:
    """Cierres de mes de `desde` a `hasta`, ambos inclusive."""
    meses: list[date] = []
    cursor = fin_de_mes(desde)
    tope = fin_de_mes(hasta)
    while cursor <= tope and len(meses) < MAX_MESES:
        meses.append(cursor)
        cursor = _mes_siguiente(cursor)
    return meses


# ---------------------------------------------------------------------------
# Lectura de posiciones a una fecha
# ---------------------------------------------------------------------------


def _ultimas_valuaciones(user_id: uuid.UUID, al: date) -> Select:
    """Ultima valuacion <= `al` de cada activo del usuario.

    `DISTINCT ON` es de Postgres y aqui se gana el uso: resuelve el "ultimo por
    grupo" en una pasada, sin la subconsulta correlacionada por fila que haria
    falta en SQL estandar.
    """
    return (
        select(
            AssetValuation.asset_id,
            AssetValuation.valor,
            AssetValuation.fecha,
            Asset.nombre,
            Asset.tipo,
            Asset.moneda,
        )
        .join(Asset, Asset.id == AssetValuation.asset_id)
        .where(AssetValuation.user_id == user_id, AssetValuation.fecha <= al)
        .distinct(AssetValuation.asset_id)
        .order_by(AssetValuation.asset_id, AssetValuation.fecha.desc())
    )


def _ultimos_saldos(user_id: uuid.UUID, al: date) -> Select:
    """Ultimo saldo <= `al` de cada deuda del usuario."""
    return (
        select(
            LiabilityBalance.liability_id,
            LiabilityBalance.saldo,
            LiabilityBalance.fecha,
            Liability.nombre,
            Liability.tipo,
            Liability.moneda,
        )
        .join(Liability, Liability.id == LiabilityBalance.liability_id)
        .where(LiabilityBalance.user_id == user_id, LiabilityBalance.fecha <= al)
        .distinct(LiabilityBalance.liability_id)
        .order_by(LiabilityBalance.liability_id, LiabilityBalance.fecha.desc())
    )


# ---------------------------------------------------------------------------
# Tasas
# ---------------------------------------------------------------------------


async def _tasa_al_cierre(
    db: AsyncSession, origen: Currency, destino: Currency, al: date
) -> tuple[Decimal, bool]:
    """Tasa para convertir `origen` a `destino` en la fecha `al`.

    Devuelve `(tasa, estimada)`. Sobre `get_rate` agrega un ultimo recurso: si
    no hay ninguna tasa con fecha <= `al` pero si existen tasas posteriores, usa
    la mas antigua conocida y marca `estimada=True`.

    El motivo es practico: alguien que carga tasas desde 2025 y registra un
    activo en dolares comprado en 2023 veria fallar la serie entera por los
    meses viejos. Usar la tasa real mas antigua que se conoce no es inventar un
    numero -- inventar seria asumir 1 -- y el flag hace que la UI lo advierta.
    Si no hay ninguna tasa en absoluto, si se falla, con instrucciones.
    """
    if origen == destino:
        return UNO, False

    try:
        lookup = await exchange_rates.get_rate(db, origen, destino, al)
    except HTTPException:
        mas_antigua = await _tasa_mas_antigua(db, origen, destino)
        if mas_antigua is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"No hay ninguna tasa de cambio {origen} -> {destino} registrada, "
                    f"y hay posiciones en {origen} que convertir al cierre de "
                    f"{al.isoformat()}. Registra al menos una en "
                    f"POST /api/v1/exchange-rates."
                ),
            ) from None
        return mas_antigua, True

    return lookup.tasa, lookup.estimada


async def _tasa_mas_antigua(
    db: AsyncSession, origen: Currency, destino: Currency
) -> Decimal | None:
    """La tasa conocida mas vieja, directa o invertida."""
    directa = await db.execute(
        select(ExchangeRate.tasa)
        .where(ExchangeRate.origen == origen, ExchangeRate.destino == destino)
        .order_by(ExchangeRate.fecha)
        .limit(1)
    )
    tasa = directa.scalar_one_or_none()
    if tasa is not None:
        return tasa

    inversa = await db.execute(
        select(ExchangeRate.tasa)
        .where(ExchangeRate.origen == destino, ExchangeRate.destino == origen)
        .order_by(ExchangeRate.fecha)
        .limit(1)
    )
    tasa = inversa.scalar_one_or_none()
    if tasa is None:
        return None
    return (UNO / tasa).quantize(Decimal("0.000000000001"))


class _CacheTasas:
    """Memoriza las tasas de un recompute.

    Sin esto, recomputar 36 meses con activos en dos monedas dispara ~72
    consultas identicas a `exchange_rates`. La clave es (moneda, cierre).
    """

    def __init__(self, db: AsyncSession, base: Currency):
        self._db = db
        self._base = base
        self._cache: dict[tuple[Currency, date], tuple[Decimal, bool]] = {}

    async def get(self, moneda: Currency, al: date) -> tuple[Decimal, bool]:
        clave = (moneda, al)
        if clave not in self._cache:
            self._cache[clave] = await _tasa_al_cierre(self._db, moneda, self._base, al)
        return self._cache[clave]


# ---------------------------------------------------------------------------
# Materializacion
# ---------------------------------------------------------------------------


async def _primera_fecha_con_datos(db: AsyncSession, user_id: uuid.UUID) -> date | None:
    """La valuacion o saldo mas antiguo del usuario: donde empieza su historia."""
    result = await db.execute(
        select(
            func.least(
                select(func.min(AssetValuation.fecha))
                .where(AssetValuation.user_id == user_id)
                .scalar_subquery(),
                select(func.min(LiabilityBalance.fecha))
                .where(LiabilityBalance.user_id == user_id)
                .scalar_subquery(),
            )
        )
    )
    return result.scalar_one_or_none()


async def recompute_snapshots(
    db: AsyncSession, user: User, desde: date | None = None
) -> RecomputeResult:
    """Rematerializa los cierres mensuales desde `desde` hasta hoy.

    `desde` acota el trabajo al tramo que pudo cambiar: si se corrige una
    valuacion de marzo, los meses anteriores no se tocan. Sin `desde` recalcula
    desde la primera valuacion o saldo del usuario.
    """
    inicio_datos = await _primera_fecha_con_datos(db, user.id)
    if inicio_datos is None:
        # Sin activos ni deudas no hay serie que materializar. Se limpian los
        # snapshots que hubieran quedado de antes para que la grafica no muestre
        # un patrimonio que ya no tiene respaldo.
        await _borrar_desde(db, user.id, desde)
        await db.commit()
        hoy = date.today()
        return RecomputeResult(desde=desde or hoy, hasta=hoy, meses=0)

    arranque = max(desde, inicio_datos) if desde else inicio_datos
    meses = _meses_entre(arranque, date.today())
    if not meses:
        return RecomputeResult(desde=arranque, hasta=date.today(), meses=0)

    tasas = _CacheTasas(db, user.moneda_base)
    existentes = await _snapshots_por_fecha(db, user.id, meses[0], meses[-1])

    for cierre in meses:
        activos, pasivos, estimada, tasa_usd = await _consolidar(db, user, cierre, tasas)
        neto = activos - pasivos

        snapshot = existentes.get(cierre)
        if snapshot is None:
            snapshot = NetWorthSnapshot(id=uuid.uuid4(), user_id=user.id, fecha=cierre)
            db.add(snapshot)

        snapshot.total_activos = activos
        snapshot.total_pasivos = pasivos
        snapshot.patrimonio_neto = neto
        snapshot.moneda_base = user.moneda_base
        snapshot.tasa_estimada = estimada
        snapshot.tasa_usd = tasa_usd

    await db.commit()
    return RecomputeResult(desde=meses[0], hasta=meses[-1], meses=len(meses))


async def _consolidar(
    db: AsyncSession, user: User, cierre: date, tasas: _CacheTasas
) -> tuple[Decimal, Decimal, bool, Decimal | None]:
    """Totales de activos y pasivos a una fecha, ya en moneda base.

    El mes en curso se consolida CON LOS DATOS DE HOY aunque el snapshot se
    feche en su cierre: el 30 de septiembre todavia no ha ocurrido, asi que no
    hay ni valuaciones ni tasa de ese dia. Sin este ajuste el punto mas reciente
    de la grafica saldria marcado como estimado durante todo el mes -- una
    advertencia permanente, y por lo tanto invisible, que es peor que ninguna.

    De paso impide que una valuacion fechada a proposito en el futuro se cuele
    en el punto de hoy.
    """
    efectiva = min(cierre, date.today())
    estimada = False
    tasa_usd: Decimal | None = None

    total_activos = CERO
    for fila in (await db.execute(_ultimas_valuaciones(user.id, efectiva))).all():
        tasa, fue_estimada = await tasas.get(fila.moneda, efectiva)
        estimada = estimada or fue_estimada
        if fila.moneda == Currency.USD:
            tasa_usd = tasa
        total_activos += fila.valor * tasa

    total_pasivos = CERO
    for fila in (await db.execute(_ultimos_saldos(user.id, efectiva))).all():
        tasa, fue_estimada = await tasas.get(fila.moneda, efectiva)
        estimada = estimada or fue_estimada
        if fila.moneda == Currency.USD:
            tasa_usd = tasa
        total_pasivos += fila.saldo * tasa

    # Se redondea al final, no en cada suma: cuantizar cada linea acumularia el
    # error de redondeo que la aritmetica decimal existe para evitar.
    activos = quantize_money(total_activos)
    pasivos = quantize_money(total_pasivos)
    return activos, pasivos, estimada, tasa_usd


async def _snapshots_por_fecha(
    db: AsyncSession, user_id: uuid.UUID, desde: date, hasta: date
) -> dict[date, NetWorthSnapshot]:
    result = await db.execute(
        select(NetWorthSnapshot).where(
            NetWorthSnapshot.user_id == user_id,
            NetWorthSnapshot.fecha >= desde,
            NetWorthSnapshot.fecha <= hasta,
        )
    )
    return {s.fecha: s for s in result.scalars().all()}


async def _borrar_desde(db: AsyncSession, user_id: uuid.UUID, desde: date | None) -> None:
    query = select(NetWorthSnapshot).where(NetWorthSnapshot.user_id == user_id)
    if desde is not None:
        query = query.where(NetWorthSnapshot.fecha >= fin_de_mes(desde))
    for snapshot in (await db.execute(query)).scalars().all():
        await db.delete(snapshot)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


async def series(
    db: AsyncSession, user: User, desde: date, hasta: date
) -> NetWorthSeries:
    """Serie mensual ya materializada. Solo lee: no recalcula."""
    result = await db.execute(
        select(NetWorthSnapshot)
        .where(
            NetWorthSnapshot.user_id == user.id,
            NetWorthSnapshot.fecha >= desde,
            NetWorthSnapshot.fecha <= hasta,
        )
        .order_by(NetWorthSnapshot.fecha)
    )
    snapshots = list(result.scalars().all())

    puntos = [
        NetWorthPoint(
            fecha=s.fecha,
            total_activos=s.total_activos,
            total_pasivos=s.total_pasivos,
            patrimonio_neto=s.patrimonio_neto,
            tasa_estimada=s.tasa_estimada,
        )
        for s in snapshots
    ]

    variacion = CERO
    variacion_pct: float | None = None
    if len(puntos) >= 2:
        inicial = puntos[0].patrimonio_neto
        final = puntos[-1].patrimonio_neto
        variacion = final - inicial
        # El porcentaje solo tiene sentido contra una base positiva: partir de
        # patrimonio cero o negativo daria un numero grande y sin significado.
        if inicial > 0:
            variacion_pct = float(variacion / inicial * 100)

    return NetWorthSeries(
        moneda_base=user.moneda_base,
        desde=desde,
        hasta=hasta,
        puntos=puntos,
        variacion=quantize_money(variacion),
        variacion_pct=variacion_pct,
    )


async def composition(db: AsyncSession, user: User, al: date | None = None) -> NetWorthComposition:
    """De que esta hecho el patrimonio a una fecha (por defecto, hoy)."""
    fecha = al or date.today()
    tasas = _CacheTasas(db, user.moneda_base)
    estimada = False
    tasa_usd: Decimal | None = None

    activos: list[CompositionItem] = []
    total_activos = CERO
    for fila in (await db.execute(_ultimas_valuaciones(user.id, fecha))).all():
        tasa, fue_estimada = await tasas.get(fila.moneda, fecha)
        estimada = estimada or fue_estimada
        if fila.moneda == Currency.USD:
            tasa_usd = tasa
        valor_base = quantize_money(fila.valor * tasa)
        total_activos += valor_base
        activos.append(
            CompositionItem(
                id=fila.asset_id,
                nombre=fila.nombre,
                tipo=str(fila.tipo),
                moneda=fila.moneda,
                valor_original=fila.valor,
                valor_base=valor_base,
                fecha_valor=fila.fecha,
                porcentaje=0.0,
            )
        )

    pasivos: list[CompositionItem] = []
    total_pasivos = CERO
    for fila in (await db.execute(_ultimos_saldos(user.id, fecha))).all():
        tasa, fue_estimada = await tasas.get(fila.moneda, fecha)
        estimada = estimada or fue_estimada
        if fila.moneda == Currency.USD:
            tasa_usd = tasa
        valor_base = quantize_money(fila.saldo * tasa)
        total_pasivos += valor_base
        pasivos.append(
            CompositionItem(
                id=fila.liability_id,
                nombre=fila.nombre,
                tipo=str(fila.tipo),
                moneda=fila.moneda,
                valor_original=fila.saldo,
                valor_base=valor_base,
                fecha_valor=fila.fecha,
                porcentaje=0.0,
            )
        )

    # El porcentaje es solo para pintar: se calcula al final, sobre el total de
    # su propio lado, y nunca vuelve a entrar en un calculo de dinero.
    for item in activos:
        item.porcentaje = float(item.valor_base / total_activos * 100) if total_activos else 0.0
    for item in pasivos:
        item.porcentaje = float(item.valor_base / total_pasivos * 100) if total_pasivos else 0.0

    activos.sort(key=lambda i: i.valor_base, reverse=True)
    pasivos.sort(key=lambda i: i.valor_base, reverse=True)

    return NetWorthComposition(
        moneda_base=user.moneda_base,
        fecha=fecha,
        total_activos=quantize_money(total_activos),
        total_pasivos=quantize_money(total_pasivos),
        patrimonio_neto=quantize_money(total_activos - total_pasivos),
        activos=activos,
        pasivos=pasivos,
        tasa_estimada=estimada,
        tasa_usd=tasa_usd,
    )
