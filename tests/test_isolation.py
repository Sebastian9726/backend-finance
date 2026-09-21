"""Aislamiento entre usuarios.

Es la prueba que evita el IDOR silencioso: una consulta a la que se le olvido
filtrar por `user_id` responde 200 con datos de otra persona y nada falla a la
vista. Se comprueba router por router, tanto en lectura como en mutacion.
"""

from tests.conftest import Usuario


async def test_cada_usuario_solo_ve_sus_cuentas(usuario: Usuario, otro_usuario: Usuario):
    await usuario.post(
        "/api/v1/accounts",
        {"nombre": "Cuenta de Ana", "tipo": "banco", "moneda": "COP", "saldo_inicial": "100.00"},
    )
    await otro_usuario.post(
        "/api/v1/accounts",
        {"nombre": "Cuenta de Beto", "tipo": "efectivo", "moneda": "COP"},
    )

    de_ana = await usuario.get("/api/v1/accounts")
    nombres = [c["nombre"] for c in de_ana.json()]
    assert nombres == ["Cuenta de Ana"]


async def test_no_se_puede_leer_ni_mutar_una_cuenta_ajena(
    usuario: Usuario, otro_usuario: Usuario, cuenta: dict
):
    ajena = f"/api/v1/accounts/{cuenta['id']}"

    # 404 y no 403: un 403 confirmaria que ese id existe, y esa confirmacion ya
    # es informacion que no le corresponde a quien pregunta.
    assert (await otro_usuario.get(ajena)).status_code == 404
    assert (await otro_usuario.patch(ajena, {"nombre": "Secuestrada"})).status_code == 404
    assert (await otro_usuario.delete(ajena)).status_code == 404

    # Y la cuenta sigue intacta.
    assert (await usuario.get(ajena)).json()["nombre"] == cuenta["nombre"]


async def test_no_se_puede_mutar_una_categoria_ajena(usuario: Usuario, otro_usuario: Usuario):
    propia = (await usuario.get("/api/v1/categories")).json()[0]
    ruta = f"/api/v1/categories/{propia['id']}"

    assert (await otro_usuario.patch(ruta, {"nombre": "Robada"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404


async def test_no_se_puede_leer_ni_mutar_una_transaccion_ajena(
    usuario: Usuario, otro_usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    creada = await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "category_id": categoria_gasto["id"],
            "tipo": "gasto",
            "monto": "-50000.00",
            "fecha": "2026-06-15",
            "descripcion": "Mercado",
        },
    )
    assert creada.status_code == 201, creada.text
    ruta = f"/api/v1/transactions/{creada.json()['id']}"

    assert (await otro_usuario.get(ruta)).status_code == 404
    assert (await otro_usuario.patch(ruta, {"descripcion": "Editada"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404

    listado = await otro_usuario.get("/api/v1/transactions")
    assert listado.json()["items"] == []


async def test_no_se_puede_registrar_en_una_cuenta_ajena(
    otro_usuario: Usuario, cuenta: dict
):
    """El caso mas facil de olvidar: el id ajeno llega en el cuerpo, no en la
    ruta, asi que un filtro por URL no lo atrapa."""
    response = await otro_usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "tipo": "gasto",
            "monto": "-1000.00",
            "fecha": "2026-06-15",
            "descripcion": "Intento",
        },
    )
    assert response.status_code == 404


async def test_no_se_puede_usar_una_categoria_ajena(
    usuario: Usuario, otro_usuario: Usuario, categoria_gasto: dict
):
    propia = (await otro_usuario.post(
        "/api/v1/accounts", {"nombre": "Suya", "tipo": "efectivo", "moneda": "COP"}
    )).json()

    response = await otro_usuario.post(
        "/api/v1/transactions",
        {
            "account_id": propia["id"],
            "category_id": categoria_gasto["id"],
            "tipo": "gasto",
            "monto": "-1000.00",
            "fecha": "2026-06-15",
            "descripcion": "Intento",
        },
    )
    assert response.status_code == 404


async def test_no_se_puede_transferir_hacia_una_cuenta_ajena(
    otro_usuario: Usuario, cuenta: dict
):
    propia = (await otro_usuario.post(
        "/api/v1/accounts", {"nombre": "Origen", "tipo": "efectivo", "moneda": "COP"}
    )).json()

    response = await otro_usuario.post(
        "/api/v1/transactions/transfer",
        {
            "cuenta_origen_id": propia["id"],
            "cuenta_destino_id": cuenta["id"],
            "monto": "1000.00",
            "fecha": "2026-06-15",
            "descripcion": "Fuga",
        },
    )
    assert response.status_code == 404


async def test_los_reportes_solo_cuentan_lo_propio(
    usuario: Usuario, otro_usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "category_id": categoria_gasto["id"],
            "tipo": "gasto",
            "monto": "-300000.00",
            "fecha": "2026-06-15",
            "descripcion": "Arriendo",
        },
    )

    periodo = {"desde": "2026-06-01", "hasta": "2026-06-30"}
    mio = (await usuario.get("/api/v1/reports/summary", periodo)).json()
    ajeno = (await otro_usuario.get("/api/v1/reports/summary", periodo)).json()

    assert mio["gastos"] == "300000.00"
    assert ajeno["gastos"] == "0.00"
    assert ajeno["transacciones"] == 0


# ---------------------------------------------------------------------------
# Patrimonio
# ---------------------------------------------------------------------------

_ACTIVO = {
    "nombre": "Apartamento",
    "tipo": "inmueble",
    "moneda": "COP",
    "valor_actual": "350000000.00",
    "fecha_valor": "2026-01-31",
}
_DEUDA = {
    "nombre": "Hipoteca",
    "tipo": "hipoteca",
    "moneda": "COP",
    "saldo_actual": "200000000.00",
    "fecha_saldo": "2026-01-31",
}


async def test_no_se_puede_leer_ni_mutar_un_activo_ajeno(
    usuario: Usuario, otro_usuario: Usuario
):
    creado = await usuario.post("/api/v1/assets", _ACTIVO)
    assert creado.status_code == 201, creado.text
    ruta = f"/api/v1/assets/{creado.json()['id']}"

    assert (await otro_usuario.get(ruta)).status_code == 404
    assert (await otro_usuario.patch(ruta, {"nombre": "Robado"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404
    assert (await otro_usuario.get("/api/v1/assets")).json() == []


async def test_no_se_puede_valuar_un_activo_ajeno(usuario: Usuario, otro_usuario: Usuario):
    """El activo llega por la ruta, pero la valuacion es una escritura nueva:
    si el servicio no revisara la propiedad, cualquiera podria mover el
    patrimonio de otro."""
    creado = await usuario.post("/api/v1/assets", _ACTIVO)
    activo_id = creado.json()["id"]

    intento = await otro_usuario.post(
        f"/api/v1/assets/{activo_id}/valuations", {"fecha": "2026-03-31", "valor": "1.00"}
    )
    assert intento.status_code == 404

    assert (await otro_usuario.get(f"/api/v1/assets/{activo_id}/valuations")).status_code == 404
    # Y el activo conserva su unica valuacion.
    assert len((await usuario.get(f"/api/v1/assets/{activo_id}")).json()["valuaciones"]) == 1


async def test_no_se_puede_leer_ni_mutar_una_deuda_ajena(
    usuario: Usuario, otro_usuario: Usuario
):
    creada = await usuario.post("/api/v1/liabilities", _DEUDA)
    assert creada.status_code == 201, creada.text
    ruta = f"/api/v1/liabilities/{creada.json()['id']}"

    assert (await otro_usuario.get(ruta)).status_code == 404
    assert (await otro_usuario.patch(ruta, {"nombre": "Ajena"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404
    assert (await otro_usuario.get("/api/v1/liabilities")).json() == []


async def test_no_se_puede_abonar_a_una_deuda_ajena(usuario: Usuario, otro_usuario: Usuario):
    creada = await usuario.post("/api/v1/liabilities", _DEUDA)
    deuda_id = creada.json()["id"]

    intento = await otro_usuario.post(
        f"/api/v1/liabilities/{deuda_id}/balances", {"fecha": "2026-03-31", "saldo": "0.00"}
    )
    assert intento.status_code == 404
    assert (await usuario.get(f"/api/v1/liabilities/{deuda_id}")).json()["saldo_actual"] == (
        "200000000.00"
    )


async def test_la_serie_de_patrimonio_solo_cuenta_lo_propio(
    usuario: Usuario, otro_usuario: Usuario
):
    await usuario.post("/api/v1/assets", _ACTIVO)
    await usuario.post("/api/v1/liabilities", _DEUDA)

    rango = {"desde": "2026-01-01", "hasta": "2026-12-31"}
    mia = (await usuario.get("/api/v1/networth/series", rango)).json()
    ajena = (await otro_usuario.get("/api/v1/networth/series", rango)).json()

    assert mia["puntos"][0]["patrimonio_neto"] == "150000000.00"
    assert ajena["puntos"] == []

    composicion = (await otro_usuario.get("/api/v1/networth/composition")).json()
    assert composicion["patrimonio_neto"] == "0.00"
    assert composicion["activos"] == []


# ---------------------------------------------------------------------------
# Planeacion
# ---------------------------------------------------------------------------

_MES = {"anio": 2026, "mes": 6}


async def test_no_se_puede_mutar_un_presupuesto_ajeno(
    usuario: Usuario, otro_usuario: Usuario, categoria_gasto: dict
):
    creado = await usuario.post(
        "/api/v1/budgets",
        {"category_id": categoria_gasto["id"], **_MES, "monto_limite": "500000.00"},
    )
    assert creado.status_code == 201, creado.text
    ruta = f"/api/v1/budgets/{creado.json()['id']}"

    assert (await otro_usuario.patch(ruta, {"monto_limite": "1.00"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404
    assert (await otro_usuario.get("/api/v1/budgets", _MES)).json() == []


async def test_no_se_puede_presupuestar_una_categoria_ajena(
    otro_usuario: Usuario, categoria_gasto: dict
):
    """El id ajeno llega en el cuerpo, no en la ruta."""
    response = await otro_usuario.post(
        "/api/v1/budgets",
        {"category_id": categoria_gasto["id"], **_MES, "monto_limite": "100.00"},
    )
    assert response.status_code == 404


async def test_el_status_de_presupuesto_solo_cuenta_lo_propio(
    usuario: Usuario, otro_usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    await usuario.post(
        "/api/v1/budgets",
        {"category_id": categoria_gasto["id"], **_MES, "monto_limite": "500000.00"},
    )
    await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "category_id": categoria_gasto["id"],
            "tipo": "gasto",
            "monto": "-200000.00",
            "fecha": "2026-06-15",
            "descripcion": "Mercado",
        },
    )

    mio = (await usuario.get("/api/v1/budgets/status", _MES)).json()
    ajeno = (await otro_usuario.get("/api/v1/budgets/status", _MES)).json()

    assert mio["total_ejecutado"] == "200000.00"
    assert ajeno["lineas"] == []
    assert ajeno["total_ejecutado"] == "0.00"
    assert ajeno["total_sin_presupuesto"] == "0.00"


async def test_no_se_puede_leer_ni_mutar_una_meta_ajena(
    usuario: Usuario, otro_usuario: Usuario
):
    creada = await usuario.post(
        "/api/v1/goals",
        {"nombre": "Viaje", "moneda": "COP", "monto_objetivo": "10000000.00"},
    )
    assert creada.status_code == 201, creada.text
    ruta = f"/api/v1/goals/{creada.json()['id']}"

    assert (await otro_usuario.get(ruta)).status_code == 404
    assert (await otro_usuario.patch(ruta, {"nombre": "Robada"})).status_code == 404
    assert (await otro_usuario.delete(ruta)).status_code == 404
    assert (await otro_usuario.get("/api/v1/goals")).json() == []


async def test_no_se_puede_aportar_a_una_meta_ajena(usuario: Usuario, otro_usuario: Usuario):
    creada = await usuario.post(
        "/api/v1/goals",
        {
            "nombre": "Ahorro",
            "moneda": "COP",
            "monto_objetivo": "5000000.00",
            "monto_inicial": "1000000.00",
        },
    )
    meta_id = creada.json()["id"]

    intento = await otro_usuario.post(
        f"/api/v1/goals/{meta_id}/contributions", {"fecha": "2026-06-15", "monto": "999.00"}
    )
    assert intento.status_code == 404
    assert (
        await otro_usuario.get(f"/api/v1/goals/{meta_id}/contributions")
    ).status_code == 404

    # Y el acumulado de la meta no se movio.
    assert (await usuario.get(f"/api/v1/goals/{meta_id}")).json()["monto_actual"] == (
        "1000000.00"
    )
