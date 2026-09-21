"""Presupuestos y metas.

Lo que se prueba: que el mes se corte donde debe (el error clasico es que
enero se coma el primero de febrero), que el semaforo dependa del umbral
guardado y no de uno inventado en la UI, y que la proyeccion de una meta salga
del ritmo observado o no salga del todo.
"""

from datetime import date, timedelta

from tests.conftest import Usuario

JUNIO = {"anio": 2026, "mes": 6}


async def _gasto(usuario: Usuario, cuenta: dict, categoria: dict, monto: str, fecha: str):
    response = await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "category_id": categoria["id"],
            "tipo": "gasto",
            "monto": monto,
            "fecha": fecha,
            "descripcion": "Gasto de prueba",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _presupuesto(usuario: Usuario, categoria: dict, limite: str, **extra):
    datos = {"category_id": categoria["id"], **JUNIO, "monto_limite": limite, **extra}
    response = await usuario.post("/api/v1/budgets", datos)
    assert response.status_code == 201, response.text
    return response.json()


async def _status(usuario: Usuario, anio: int = 2026, mes: int = 6) -> dict:
    response = await usuario.get("/api/v1/budgets/status", {"anio": anio, "mes": mes})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Presupuestos
# ---------------------------------------------------------------------------


async def test_el_mes_del_presupuesto_no_se_come_el_dia_siguiente(
    usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    """La prueba que pide el plan: transacciones a caballo entre dos meses.

    Un `< primero_del_siguiente` mal escrito arrastra el primero de julio al
    presupuesto de junio, y nadie lo nota hasta que el limite se pasa solo.
    """
    await _presupuesto(usuario, categoria_gasto, "1000000.00")

    await _gasto(usuario, cuenta, categoria_gasto, "-100000.00", "2026-05-31")  # mes anterior
    await _gasto(usuario, cuenta, categoria_gasto, "-200000.00", "2026-06-01")  # primer dia
    await _gasto(usuario, cuenta, categoria_gasto, "-300000.00", "2026-06-30")  # ultimo dia
    await _gasto(usuario, cuenta, categoria_gasto, "-400000.00", "2026-07-01")  # mes siguiente

    status = await _status(usuario)
    linea = status["lineas"][0]

    # Solo los dos de junio: 200.000 + 300.000
    assert linea["ejecutado"] == "500000.00"
    assert linea["disponible"] == "500000.00"
    assert status["desde"] == "2026-06-01"
    assert status["hasta"] == "2026-06-30"


async def test_el_semaforo_usa_el_umbral_guardado(
    usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    await _presupuesto(usuario, categoria_gasto, "1000000.00", alerta_pct="75")

    # 70% -> todavia en verde con umbral de 75.
    await _gasto(usuario, cuenta, categoria_gasto, "-700000.00", "2026-06-10")
    assert (await _status(usuario))["lineas"][0]["estado"] == "ok"

    # 80% -> pasa el umbral.
    await _gasto(usuario, cuenta, categoria_gasto, "-100000.00", "2026-06-11")
    assert (await _status(usuario))["lineas"][0]["estado"] == "alerta"

    # 110% -> excedido, y `disponible` queda negativo.
    await _gasto(usuario, cuenta, categoria_gasto, "-300000.00", "2026-06-12")
    linea = (await _status(usuario))["lineas"][0]
    assert linea["estado"] == "excedido"
    assert linea["disponible"] == "-100000.00"


async def test_el_gasto_sin_presupuesto_se_reporta_aparte(
    usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    """Presupuestar tres categorias y no ver el gasto de las otras diez es la
    forma mas comun de sentirse cubierto sin estarlo."""
    categorias = (await usuario.get("/api/v1/categories", {"tipo": "gasto"})).json()
    otra = next(c for c in categorias if c["id"] != categoria_gasto["id"])

    await _presupuesto(usuario, categoria_gasto, "1000000.00")
    await _gasto(usuario, cuenta, categoria_gasto, "-400000.00", "2026-06-10")
    await _gasto(usuario, cuenta, otra, "-250000.00", "2026-06-11")

    status = await _status(usuario)
    assert status["total_ejecutado"] == "400000.00"
    assert status["total_sin_presupuesto"] == "250000.00"
    assert [s["categoria"] for s in status["sin_presupuesto"]] == [otra["nombre"]]


async def test_los_centavos_del_presupuesto_sobreviven(
    usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    await _presupuesto(usuario, categoria_gasto, "1234567.89")
    await _gasto(usuario, cuenta, categoria_gasto, "-234567.89", "2026-06-10")

    linea = (await _status(usuario))["lineas"][0]
    assert linea["monto_limite"] == "1234567.89"
    assert linea["ejecutado"] == "234567.89"
    assert linea["disponible"] == "1000000.00"


async def test_no_se_presupuesta_una_categoria_de_ingreso(usuario: Usuario):
    ingreso = (await usuario.get("/api/v1/categories", {"tipo": "ingreso"})).json()[0]
    response = await usuario.post(
        "/api/v1/budgets",
        {"category_id": ingreso["id"], **JUNIO, "monto_limite": "100.00"},
    )
    assert response.status_code == 422
    assert "gasto" in response.json()["detail"]


async def test_presupuesto_repetido_para_la_misma_categoria_y_mes_da_409(
    usuario: Usuario, categoria_gasto: dict
):
    await _presupuesto(usuario, categoria_gasto, "100000.00")
    repetido = await usuario.post(
        "/api/v1/budgets",
        {"category_id": categoria_gasto["id"], **JUNIO, "monto_limite": "200000.00"},
    )
    assert repetido.status_code == 409


async def test_copiar_del_mes_anterior_no_pisa_lo_ya_ajustado(
    usuario: Usuario, categoria_gasto: dict
):
    """Se puede repetir sin miedo: lo que ya existe se omite."""
    categorias = (await usuario.get("/api/v1/categories", {"tipo": "gasto"})).json()
    otra = next(c for c in categorias if c["id"] != categoria_gasto["id"])

    await _presupuesto(usuario, categoria_gasto, "1000000.00")
    await _presupuesto(usuario, otra, "500000.00")

    # Julio ya tiene uno ajustado a mano para la primera categoria.
    await usuario.post(
        "/api/v1/budgets",
        {"category_id": categoria_gasto["id"], "anio": 2026, "mes": 7, "monto_limite": "1.00"},
    )

    copia = await usuario.post("/api/v1/budgets/copy?anio=2026&mes=7", {})
    assert copia.status_code == 200, copia.text
    assert copia.json() == {"anio": 2026, "mes": 7, "copiados": 1, "omitidos": 1}

    julio = (await usuario.get("/api/v1/budgets", {"anio": 2026, "mes": 7})).json()
    limites = {b["category_id"]: b["monto_limite"] for b in julio}
    assert limites[categoria_gasto["id"]] == "1.00"  # intacto
    assert limites[otra["id"]] == "500000.00"  # copiado

    # Repetirla no duplica nada.
    otra_vez = await usuario.post("/api/v1/budgets/copy?anio=2026&mes=7", {})
    assert otra_vez.json()["copiados"] == 0


async def test_copiar_sin_mes_anterior_da_404(usuario: Usuario):
    response = await usuario.post("/api/v1/budgets/copy?anio=2026&mes=7", {})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Metas
# ---------------------------------------------------------------------------


async def test_la_meta_acumula_sus_aportes(usuario: Usuario):
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {"nombre": "Viaje", "moneda": "COP", "monto_objetivo": "10000000.00"},
        )
    ).json()
    assert meta["monto_actual"] == "0.00"
    assert meta["porcentaje"] == 0.0

    for monto in ("2000000.00", "1500000.00"):
        response = await usuario.post(
            f"/api/v1/goals/{meta['id']}/contributions",
            {"fecha": "2026-06-10", "monto": monto},
        )
        assert response.status_code == 201, response.text

    detalle = (await usuario.get(f"/api/v1/goals/{meta['id']}")).json()
    # Varios aportes el mismo dia se SUMAN: son eventos, no fotos del estado.
    assert len(detalle["aportes"]) == 2
    assert detalle["monto_actual"] == "3500000.00"
    assert detalle["monto_faltante"] == "6500000.00"
    assert detalle["porcentaje"] == 35.0


async def test_el_monto_inicial_entra_como_primer_aporte(usuario: Usuario):
    """Asi la meta arranca con historia y el ritmo se puede medir."""
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {
                "nombre": "Carro",
                "moneda": "COP",
                "monto_objetivo": "40000000.00",
                "monto_inicial": "5000000.00",
            },
        )
    ).json()
    assert meta["monto_actual"] == "5000000.00"
    assert len(meta["aportes"]) == 1
    assert meta["aportes"][0]["nota"] == "Ahorro inicial"


async def test_un_retiro_baja_el_acumulado(usuario: Usuario):
    """Sacar plata de la meta es un retiro, no un error: se registra en
    negativo en vez de borrar aportes y perder la historia."""
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {
                "nombre": "Fondo",
                "moneda": "COP",
                "monto_objetivo": "5000000.00",
                "monto_inicial": "3000000.00",
            },
        )
    ).json()

    await usuario.post(
        f"/api/v1/goals/{meta['id']}/contributions",
        {"fecha": "2026-06-15", "monto": "-1000000.00", "nota": "Imprevisto"},
    )

    detalle = (await usuario.get(f"/api/v1/goals/{meta['id']}")).json()
    assert detalle["monto_actual"] == "2000000.00"


async def test_sin_historia_suficiente_no_se_proyecta_una_fecha(usuario: Usuario):
    """Aportar hoy no significa un ritmo: una adivinanza presentada como dato
    es peor que no decir nada."""
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {
                "nombre": "Nueva",
                "moneda": "COP",
                "monto_objetivo": "10000000.00",
                "monto_inicial": "1000000.00",
            },
        )
    ).json()

    assert meta["aporte_mensual_promedio"] is None
    assert meta["fecha_proyectada"] is None
    assert meta["en_riesgo"] is False


async def test_con_ritmo_observado_si_se_proyecta(usuario: Usuario):
    """Dos meses de aportes constantes dan un ritmo y una fecha creible."""
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {"nombre": "Estudio", "moneda": "COP", "monto_objetivo": "12000000.00"},
        )
    ).json()

    hoy = date.today()
    # 1.000.000 hace 60 dias y otro hace 30: ~1.000.000 al mes.
    for atras in (60, 30):
        await usuario.post(
            f"/api/v1/goals/{meta['id']}/contributions",
            {"fecha": (hoy - timedelta(days=atras)).isoformat(), "monto": "1000000.00"},
        )

    detalle = (await usuario.get(f"/api/v1/goals/{meta['id']}")).json()
    assert detalle["monto_actual"] == "2000000.00"
    assert detalle["aporte_mensual_promedio"] is not None
    assert detalle["fecha_proyectada"] is not None
    # Faltan 10.000.000 a ~1.000.000/mes: la fecha cae bastante adelante.
    assert detalle["fecha_proyectada"] > hoy.isoformat()


async def test_una_meta_lenta_para_su_fecha_queda_en_riesgo(usuario: Usuario):
    hoy = date.today()
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {
                "nombre": "Apretada",
                "moneda": "COP",
                "monto_objetivo": "50000000.00",
                # Un mes para juntar lo que falta, al ritmo de 1M/mes.
                "fecha_objetivo": (hoy + timedelta(days=30)).isoformat(),
            },
        )
    ).json()

    for atras in (60, 30):
        await usuario.post(
            f"/api/v1/goals/{meta['id']}/contributions",
            {"fecha": (hoy - timedelta(days=atras)).isoformat(), "monto": "1000000.00"},
        )

    detalle = (await usuario.get(f"/api/v1/goals/{meta['id']}")).json()
    assert detalle["en_riesgo"] is True
    # Y dice cuanto habria que aportar al mes para alcanzarla.
    assert detalle["aporte_mensual_requerido"] is not None


async def test_una_meta_cumplida_no_desborda_la_barra(usuario: Usuario):
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {
                "nombre": "Cumplida",
                "moneda": "COP",
                "monto_objetivo": "1000000.00",
                "monto_inicial": "1500000.00",
            },
        )
    ).json()

    assert meta["cumplida"] is True
    assert meta["porcentaje"] == 100.0
    assert meta["monto_faltante"] == "0.00"
    assert meta["fecha_proyectada"] is None


async def test_la_meta_no_acepta_aporte_en_cero(usuario: Usuario):
    meta = (
        await usuario.post(
            "/api/v1/goals",
            {"nombre": "Meta", "moneda": "COP", "monto_objetivo": "100.00"},
        )
    ).json()

    response = await usuario.post(
        f"/api/v1/goals/{meta['id']}/contributions", {"fecha": "2026-06-10", "monto": "0"}
    )
    assert response.status_code == 422


async def test_no_se_puede_ligar_una_meta_a_una_cuenta_ajena(
    otro_usuario: Usuario, cuenta: dict
):
    """El id ajeno llega en el CUERPO, que es donde se olvida revisar."""
    response = await otro_usuario.post(
        "/api/v1/goals",
        {
            "nombre": "Intento",
            "moneda": "COP",
            "monto_objetivo": "100.00",
            "account_id": cuenta["id"],
        },
    )
    assert response.status_code == 404
