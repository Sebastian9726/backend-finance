"""Siembra un usuario de demostracion con seis meses de movimientos.

Sirve para que las graficas tengan algo que mostrar mientras se desarrolla.
Es idempotente: si el usuario ya existe, borra sus datos y los vuelve a crear.

    cd backend
    .venv/Scripts/python.exe scripts/seed_demo.py
"""

import asyncio
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountType,
    Asset,
    AssetType,
    AssetValuation,
    Category,
    Currency,
    ExchangeRate,
    Liability,
    LiabilityBalance,
    LiabilityType,
    Transaction,
    TransactionType,
    User,
)
from app.schemas.base import quantize_money  # noqa: E402
from app.services.categories import seed_default_categories  # noqa: E402
from app.services.networth import recompute_snapshots  # noqa: E402

EMAIL = "demo@ejemplo.com"
PASSWORD = "demo-finanzas-2026"
MESES = 6

# (categoria, descripcion, minimo, maximo, veces por mes)
GASTOS = [
    ("Vivienda", "Arriendo", 1_800_000, 1_800_000, 1),
    ("Servicios publicos", "Energia y agua", 180_000, 320_000, 1),
    ("Servicios publicos", "Internet y celular", 120_000, 140_000, 1),
    ("Alimentacion", "Mercado", 250_000, 480_000, 3),
    ("Alimentacion", "Almuerzo fuera", 22_000, 55_000, 6),
    ("Transporte", "Gasolina", 90_000, 160_000, 3),
    ("Transporte", "Peajes y parqueadero", 15_000, 45_000, 4),
    ("Salud", "Medicina prepagada", 260_000, 260_000, 1),
    ("Entretenimiento", "Cine y salidas", 40_000, 120_000, 2),
    ("Entretenimiento", "Suscripciones", 22_000, 45_000, 2),
    ("Ropa", "Ropa y calzado", 90_000, 280_000, 1),
    ("Educacion", "Curso en linea", 80_000, 180_000, 1),
]

INGRESOS = [
    ("Salario", "Salario mensual", 6_500_000, 6_500_000, 1),
    ("Honorarios", "Proyecto freelance", 800_000, 2_400_000, 1),
]

# (nombre, tipo, moneda, valor inicial, variacion mensual, costo de adquisicion)
# La variacion es el porcentaje que se mueve cada mes: el inmueble se valoriza
# despacio, el carro se deprecia y el portafolio sube con ruido.
ACTIVOS = [
    ("Apartamento", AssetType.INMUEBLE, Currency.COP, 320_000_000, "0.6", 280_000_000),
    ("Carro", AssetType.VEHICULO, Currency.COP, 62_000_000, "-0.9", 78_000_000),
    ("Portafolio acciones", AssetType.INVERSION, Currency.COP, 18_000_000, "1.4", 15_000_000),
    # En dolares: es el que hace visible que cada mes usa SU tasa de cambio.
    ("Cuenta de ahorro USD", AssetType.AHORRO, Currency.USD, 4_500, "0.8", None),
]

# (nombre, tipo, saldo inicial, abono mensual, principal, tasa, cuota)
DEUDAS = [
    ("Credito hipotecario", LiabilityType.HIPOTECA, 185_000_000, 1_150_000, 210_000_000,
     "11.8", 1_950_000),
    ("Credito de vehiculo", LiabilityType.VEHICULO, 24_000_000, 900_000, 45_000_000,
     "16.5", 1_180_000),
]


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as db:
        await _limpiar(db)
        user = await _crear_usuario(db)
        cuentas = await _crear_cuentas(db, user)
        await _crear_tasas(db)
        categorias = await _mapa_categorias(db, user)
        total = await _crear_movimientos(db, user, cuentas, categorias)
        activos = await _crear_activos(db, user)
        deudas = await _crear_deudas(db, user)
        await db.commit()

        # La serie se materializa al final, cuando ya existen todas las
        # valuaciones y saldos: asi se recorre una sola vez cada cierre de mes.
        serie = await recompute_snapshots(db, user)

    await engine.dispose()

    print(f"Usuario:    {EMAIL}")
    print(f"Contrasena: {PASSWORD}")
    print(f"Creadas:    {total} transacciones en {MESES} meses")
    print(f"Patrimonio: {activos} activos, {deudas} deudas, {serie.meses} cierres mensuales")


async def _limpiar(db: AsyncSession) -> None:
    existente = await db.scalar(select(User).where(User.email == EMAIL))
    if existente is not None:
        # ON DELETE CASCADE se lleva cuentas, categorias y transacciones.
        await db.execute(delete(User).where(User.id == existente.id))
        await db.commit()


async def _crear_usuario(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=EMAIL,
        password_hash=hash_password(PASSWORD),
        nombre="Usuario Demo",
        moneda_base=Currency.COP,
    )
    db.add(user)
    await db.flush()
    await seed_default_categories(db, user.id)
    await db.flush()
    return user


async def _crear_cuentas(db: AsyncSession, user: User) -> dict[str, Account]:
    definiciones = [
        ("Bancolombia", AccountType.BANCO, Currency.COP, "4200000.00"),
        # El efectivo arranca con suficiente para cubrir seis meses de gastos
        # menores: un saldo de efectivo negativo no existe en la vida real.
        ("Efectivo", AccountType.EFECTIVO, Currency.COP, "3500000.00"),
        ("Tarjeta Visa", AccountType.TARJETA_CREDITO, Currency.COP, "0.00"),
        ("Ahorros USD", AccountType.BANCO, Currency.USD, "1200.00"),
    ]
    cuentas = {}
    for nombre, tipo, moneda, saldo in definiciones:
        cuenta = Account(
            id=uuid.uuid4(),
            user_id=user.id,
            nombre=nombre,
            tipo=tipo,
            moneda=moneda,
            saldo_inicial=Decimal(saldo),
        )
        db.add(cuenta)
        cuentas[nombre] = cuenta
    await db.flush()
    return cuentas


async def _crear_tasas(db: AsyncSession) -> None:
    """Tasas al inicio Y al cierre de cada mes, con variacion realista.

    El cierre importa: los snapshots de patrimonio se fechan el ultimo dia del
    mes, y si solo hubiera tasa del dia 1 el lookup tomaria la del mes anterior
    y marcaria `tasa_estimada` en toda la serie -- una advertencia correcta pero
    que, salida de datos de demostracion, seria puro ruido.

    Las tasas son globales, no del usuario, asi que borrar el usuario demo no
    se las lleva. Por eso van con ON CONFLICT: sin el, la segunda corrida del
    script chocaria contra la restriccion unica.
    """
    hoy = date.today()
    tasa = Decimal("3950.00")
    filas = []
    fechas: list[date] = []
    for i in range(MESES, -1, -1):
        fechas.append(_primer_dia(hoy, i))
        fechas.append(_fin_de_mes_o_hoy(hoy, i))

    for fecha in sorted(set(fechas)):
        filas.append(
            {
                "id": uuid.uuid4(),
                "fecha": fecha,
                "origen": Currency.USD.value,
                "destino": Currency.COP.value,
                "tasa": tasa,
            }
        )
        tasa += Decimal(random.randint(-90, 130))

    statement = pg_insert(ExchangeRate).values(filas)
    await db.execute(
        statement.on_conflict_do_update(
            constraint="uq_exchange_rates_fecha_origen_destino",
            set_={"tasa": statement.excluded.tasa},
        )
    )
    await db.flush()


async def _mapa_categorias(db: AsyncSession, user: User) -> dict[str, Category]:
    result = await db.execute(select(Category).where(Category.user_id == user.id))
    return {c.nombre: c for c in result.scalars().all()}


async def _crear_movimientos(
    db: AsyncSession,
    user: User,
    cuentas: dict[str, Account],
    categorias: dict[str, Category],
) -> int:
    hoy = date.today()
    banco = cuentas["Bancolombia"]
    efectivo = cuentas["Efectivo"]
    tarjeta = cuentas["Tarjeta Visa"]
    total = 0

    for atras in range(MESES - 1, -1, -1):
        inicio = _primer_dia(hoy, atras)
        dias = (_primer_dia(hoy, atras - 1) - inicio).days if atras > 0 else hoy.day

        for nombre_cat, descripcion, minimo, maximo, veces in INGRESOS:
            for _ in range(veces):
                total += _agregar(
                    db, user, banco, categorias[nombre_cat], TransactionType.INGRESO,
                    descripcion, minimo, maximo, inicio, dias,
                )

        for nombre_cat, descripcion, minimo, maximo, veces in GASTOS:
            for _ in range(veces):
                # Los gastos grandes salen del banco. Los pequenos van sobre
                # todo a la tarjeta (que si puede quedar en negativo: eso es la
                # deuda) y de vez en cuando en efectivo.
                if maximo > 200_000:
                    cuenta = banco
                else:
                    cuenta = efectivo if random.random() < 0.3 else tarjeta
                total += _agregar(
                    db, user, cuenta, categorias[nombre_cat], TransactionType.GASTO,
                    descripcion, minimo, maximo, inicio, dias,
                )

    await db.flush()
    return total


def _agregar(
    db: AsyncSession,
    user: User,
    cuenta: Account,
    categoria: Category,
    tipo: TransactionType,
    descripcion: str,
    minimo: int,
    maximo: int,
    inicio: date,
    dias: int,
) -> int:
    monto = Decimal(random.randint(minimo, maximo))
    # Centavos deliberados: dejan a la vista si algun redondeo se cuela.
    monto += Decimal(random.randint(0, 99)) / 100
    monto = quantize_money(monto)
    if tipo is TransactionType.GASTO:
        monto = -monto

    db.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            account_id=cuenta.id,
            category_id=categoria.id,
            tipo=tipo,
            monto=monto,
            moneda=cuenta.moneda,
            # Todas las cuentas sembradas con movimientos son en COP, que es la
            # moneda base: la tasa es 1 y no hace falta consultarla.
            tasa_a_base=Decimal("1"),
            monto_base=monto,
            fecha=inicio + timedelta(days=random.randint(0, max(dias - 1, 0))),
            descripcion=descripcion,
        )
    )
    return 1


async def _crear_activos(db: AsyncSession, user: User) -> int:
    """Activos con una valuacion por mes, para que la grafica tenga pendiente.

    Sin varias valuaciones la serie seria una linea plana y no se veria si el
    recalculo por cierre de mes funciona.
    """
    hoy = date.today()
    for nombre, tipo, moneda, valor_inicial, variacion_pct, costo in ACTIVOS:
        asset = Asset(
            id=uuid.uuid4(),
            user_id=user.id,
            nombre=nombre,
            tipo=tipo,
            moneda=moneda,
            costo_adquisicion=Decimal(costo) if costo else None,
            fecha_adquisicion=_primer_dia(hoy, MESES),
        )
        db.add(asset)

        valor = Decimal(valor_inicial)
        factor = Decimal("1") + Decimal(variacion_pct) / 100
        for atras in range(MESES, -1, -1):
            db.add(
                AssetValuation(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    asset_id=asset.id,
                    fecha=_fin_de_mes_o_hoy(hoy, atras),
                    valor=quantize_money(valor),
                    nota="Valuacion mensual",
                )
            )
            # Un poco de ruido para que la curva no sea una recta perfecta.
            valor = valor * factor * (Decimal("1") + Decimal(random.randint(-30, 30)) / 10_000)

    await db.flush()
    return len(ACTIVOS)


async def _crear_deudas(db: AsyncSession, user: User) -> int:
    """Deudas que se van abonando mes a mes, hasta un piso de cero."""
    hoy = date.today()
    for nombre, tipo, saldo_inicial, abono, principal, tasa, cuota in DEUDAS:
        liability = Liability(
            id=uuid.uuid4(),
            user_id=user.id,
            nombre=nombre,
            tipo=tipo,
            moneda=Currency.COP,
            principal=Decimal(principal),
            tasa_interes=Decimal(tasa),
            cuota_mensual=Decimal(cuota),
            fecha_inicio=_primer_dia(hoy, MESES + 18),
        )
        db.add(liability)

        saldo = Decimal(saldo_inicial)
        for atras in range(MESES, -1, -1):
            db.add(
                LiabilityBalance(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    liability_id=liability.id,
                    fecha=_fin_de_mes_o_hoy(hoy, atras),
                    saldo=quantize_money(saldo),
                    nota="Extracto mensual",
                )
            )
            # El CHECK de la tabla no admite saldos negativos, y con razon: una
            # deuda pagada vale cero, no menos que cero.
            saldo = max(saldo - Decimal(abono), Decimal("0"))

    await db.flush()
    return len(DEUDAS)


def _fin_de_mes_o_hoy(referencia: date, meses_atras: int) -> date:
    """Ultimo dia del mes, salvo el mes en curso, que se fecha hoy.

    Fechar el mes actual en su ultimo dia pondria la valuacion en el futuro, y
    el snapshot del cierre todavia no ha ocurrido.
    """
    if meses_atras == 0:
        return referencia
    return _primer_dia(referencia, meses_atras - 1) - timedelta(days=1)


def _primer_dia(referencia: date, meses_atras: int) -> date:
    mes = referencia.month - meses_atras
    anio = referencia.year
    while mes <= 0:
        mes += 12
        anio -= 1
    while mes > 12:
        mes -= 12
        anio += 1
    return date(anio, mes, 1)


if __name__ == "__main__":
    asyncio.run(main())
