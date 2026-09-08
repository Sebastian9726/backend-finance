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
