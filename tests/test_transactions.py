"""Transacciones, transferencias y exactitud de los montos."""

from decimal import Decimal

from tests.conftest import Usuario

PERIODO = {"desde": "2026-06-01", "hasta": "2026-06-30"}


async def _gasto(usuario: Usuario, cuenta: dict, monto: str, **extra):
    return await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "tipo": "gasto",
            "monto": monto,
            "fecha": "2026-06-15",
            "descripcion": extra.pop("descripcion", "Gasto"),
            **extra,
        },
    )


async def _ingreso(usuario: Usuario, cuenta: dict, monto: str, **extra):
    return await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "tipo": "ingreso",
            "monto": monto,
            "fecha": "2026-06-15",
            "descripcion": extra.pop("descripcion", "Ingreso"),
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# Exactitud del dinero
# ---------------------------------------------------------------------------


async def test_los_centavos_sobreviven_el_viaje_redondo(usuario: Usuario, cuenta: dict):
    """El monto que entra debe ser identico al que sale, hasta el centavo."""
    response = await _gasto(usuario, cuenta, "-1234567.89")
    assert response.status_code == 201, response.text
    assert response.json()["monto"] == "-1234567.89"

    leida = await usuario.get(f"/api/v1/transactions/{response.json()['id']}")
    assert leida.json()["monto"] == "-1234567.89"


async def test_los_montos_viajan_como_string_no_como_numero(usuario: Usuario, cuenta: dict):
    """Si salieran como numero JSON, `JSON.parse` los convertiria a double en
    el navegador y ahi se perderian los centavos."""
    response = await _gasto(usuario, cuenta, "-0.01")
    body = response.json()

    assert isinstance(body["monto"], str)
    assert isinstance(body["monto_base"], str)
    assert isinstance(body["tasa_a_base"], str)


async def test_cien_centavos_suman_exactamente_un_peso(usuario: Usuario, cuenta: dict):
    for _ in range(100):
        assert (await _gasto(usuario, cuenta, "-0.01")).status_code == 201

    resumen = await usuario.get("/api/v1/reports/summary", PERIODO)
    assert resumen.json()["gastos"] == "1.00"


# ---------------------------------------------------------------------------
# Reglas de signo
# ---------------------------------------------------------------------------


async def test_el_signo_debe_coincidir_con_el_tipo(usuario: Usuario, cuenta: dict):
    # Gasto positivo: rechazado.
    assert (await _gasto(usuario, cuenta, "5000.00")).status_code == 422
    # Ingreso negativo: rechazado.
    assert (await _ingreso(usuario, cuenta, "-5000.00")).status_code == 422
    # Monto cero: rechazado.
    assert (await _gasto(usuario, cuenta, "0.00")).status_code == 422


async def test_las_transferencias_no_se_crean_por_el_endpoint_general(
    usuario: Usuario, cuenta: dict
):
    response = await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "tipo": "transferencia",
            "monto": "1000.00",
            "fecha": "2026-06-15",
            "descripcion": "Intento",
        },
    )
    assert response.status_code == 422


async def test_la_categoria_debe_ser_del_mismo_tipo(usuario: Usuario, cuenta: dict):
    ingreso_cat = (await usuario.get("/api/v1/categories", {"tipo": "ingreso"})).json()[0]

    response = await _gasto(usuario, cuenta, "-1000.00", category_id=ingreso_cat["id"])
    assert response.status_code == 422
    assert "ingreso" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Saldos
# ---------------------------------------------------------------------------


async def test_el_saldo_refleja_los_movimientos(usuario: Usuario, cuenta: dict):
    assert cuenta["saldo_actual"] == "1000000.00"

    await _gasto(usuario, cuenta, "-250000.50")
    await _ingreso(usuario, cuenta, "100000.25")

    actualizada = await usuario.get(f"/api/v1/accounts/{cuenta['id']}")
    # 1.000.000,00 - 250.000,50 + 100.000,25
    assert actualizada.json()["saldo_actual"] == "849999.75"


async def test_borrar_una_transaccion_devuelve_el_saldo(usuario: Usuario, cuenta: dict):
    creada = await _gasto(usuario, cuenta, "-300000.00")
    await usuario.delete(f"/api/v1/transactions/{creada.json()['id']}")

    actualizada = await usuario.get(f"/api/v1/accounts/{cuenta['id']}")
    assert actualizada.json()["saldo_actual"] == "1000000.00"


async def test_transferencia_entre_cuentas_de_la_misma_moneda(usuario: Usuario, cuenta: dict):
    destino = (
        await usuario.post(
            "/api/v1/accounts",
            {"nombre": "Efectivo", "tipo": "efectivo", "moneda": "COP", "saldo_inicial": "0.00"},
        )
    ).json()

    response = await usuario.post(
        "/api/v1/transactions/transfer",
        {
            "cuenta_origen_id": cuenta["id"],
            "cuenta_destino_id": destino["id"],
            "monto": "400000.00",
            "fecha": "2026-06-15",
            "descripcion": "Retiro para gastos",
        },
    )
    assert response.status_code == 201, response.text
    patas = response.json()
    assert len(patas) == 2
    assert patas[0]["monto"] == "-400000.00"
    assert patas[1]["monto"] == "400000.00"
    # Las dos patas comparten el mismo grupo: es lo que las mantiene ligadas.
    assert patas[0]["transfer_group_id"] == patas[1]["transfer_group_id"]

    origen_final = (await usuario.get(f"/api/v1/accounts/{cuenta['id']}")).json()
    destino_final = (await usuario.get(f"/api/v1/accounts/{destino['id']}")).json()
    assert origen_final["saldo_actual"] == "600000.00"
    assert destino_final["saldo_actual"] == "400000.00"


async def test_borrar_una_pata_borra_la_transferencia_completa(usuario: Usuario, cuenta: dict):
    """Dejar media transferencia descuadraria los saldos de las dos cuentas."""
    destino = (
        await usuario.post(
            "/api/v1/accounts", {"nombre": "Efectivo", "tipo": "efectivo", "moneda": "COP"}
        )
    ).json()
    patas = (
        await usuario.post(
            "/api/v1/transactions/transfer",
            {
                "cuenta_origen_id": cuenta["id"],
                "cuenta_destino_id": destino["id"],
                "monto": "100000.00",
                "fecha": "2026-06-15",
                "descripcion": "Traslado",
            },
        )
    ).json()

    await usuario.delete(f"/api/v1/transactions/{patas[0]['id']}")

    restantes = await usuario.get("/api/v1/transactions")
    assert restantes.json()["total"] == 0
    assert (await usuario.get(f"/api/v1/accounts/{cuenta['id']}")).json()[
        "saldo_actual"
    ] == "1000000.00"


async def test_las_transferencias_no_cuentan_como_ingreso_ni_gasto(
    usuario: Usuario, cuenta: dict
):
    destino = (
        await usuario.post(
            "/api/v1/accounts", {"nombre": "Efectivo", "tipo": "efectivo", "moneda": "COP"}
        )
    ).json()
    await usuario.post(
        "/api/v1/transactions/transfer",
        {
            "cuenta_origen_id": cuenta["id"],
            "cuenta_destino_id": destino["id"],
            "monto": "500000.00",
            "fecha": "2026-06-15",
            "descripcion": "Traslado",
        },
    )

    resumen = (await usuario.get("/api/v1/reports/summary", PERIODO)).json()
    assert resumen["ingresos"] == "0.00"
    assert resumen["gastos"] == "0.00"


async def test_no_se_borra_una_cuenta_con_movimientos(usuario: Usuario, cuenta: dict):
    await _gasto(usuario, cuenta, "-1000.00")
    response = await usuario.delete(f"/api/v1/accounts/{cuenta['id']}")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Multi-moneda
# ---------------------------------------------------------------------------


async def test_un_gasto_en_dolares_se_convierte_con_la_tasa_del_dia(usuario: Usuario):
    await usuario.post(
        "/api/v1/exchange-rates",
        {"fecha": "2026-06-01", "origen": "USD", "destino": "COP", "tasa": "4000.00000000"},
    )
    cuenta_usd = (
        await usuario.post(
            "/api/v1/accounts", {"nombre": "Ahorros USD", "tipo": "banco", "moneda": "USD"}
        )
    ).json()

    response = await _gasto(usuario, cuenta_usd, "-100.00")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["moneda"] == "USD"
    assert body["monto"] == "-100.00"
    # 100 USD x 4000 = 400.000 COP
    assert body["monto_base"] == "-400000.00"


async def test_sin_tasa_registrada_la_api_lo_dice_en_vez_de_inventarla(usuario: Usuario):
    """Asumir una tasa de 1 contaminaria el historico en silencio."""
    cuenta_usd = (
        await usuario.post(
            "/api/v1/accounts", {"nombre": "Ahorros USD", "tipo": "banco", "moneda": "USD"}
        )
    ).json()

    response = await _gasto(usuario, cuenta_usd, "-100.00")
    assert response.status_code == 409
    assert "tasa de cambio" in response.json()["detail"]


async def test_la_tasa_de_un_dia_anterior_se_marca_como_estimada(usuario: Usuario):
    await usuario.post(
        "/api/v1/exchange-rates",
        {"fecha": "2026-06-01", "origen": "USD", "destino": "COP", "tasa": "4000.00000000"},
    )

    consulta = await usuario.get(
        "/api/v1/exchange-rates/lookup",
        {"origen": "USD", "destino": "COP", "fecha": "2026-06-20"},
    )
    body = consulta.json()
    assert body["estimada"] is True
    assert body["fecha_aplicada"] == "2026-06-01"

    exacta = await usuario.get(
        "/api/v1/exchange-rates/lookup",
        {"origen": "USD", "destino": "COP", "fecha": "2026-06-01"},
    )
    assert exacta.json()["estimada"] is False


async def test_el_saldo_consolidado_convierte_antes_de_sumar(usuario: Usuario, cuenta: dict):
    """Sumar pesos con dolares daria un numero sin significado."""
    await usuario.post(
        "/api/v1/exchange-rates",
        {"fecha": "2026-06-01", "origen": "USD", "destino": "COP", "tasa": "4000.00000000"},
    )
    await usuario.post(
        "/api/v1/accounts",
        {"nombre": "Ahorros USD", "tipo": "banco", "moneda": "USD", "saldo_inicial": "500.00"},
    )

    resumen = (await usuario.get("/api/v1/reports/summary", PERIODO)).json()
    # 1.000.000 COP + (500 USD x 4000) = 3.000.000 COP
    assert resumen["saldo_total"] == "3000000.00"
    assert resumen["moneda_base"] == "COP"


# ---------------------------------------------------------------------------
# Edicion y reportes
# ---------------------------------------------------------------------------


async def test_cambiar_la_fecha_recalcula_la_conversion(usuario: Usuario):
    """La tasa se congela al registrar, pero corregir la fecha significa que el
    movimiento ocurrio otro dia, y ese dia tenia otra tasa."""
    for fecha, tasa in [("2026-06-01", "4000.00000000"), ("2026-06-20", "4500.00000000")]:
        await usuario.post(
            "/api/v1/exchange-rates",
            {"fecha": fecha, "origen": "USD", "destino": "COP", "tasa": tasa},
        )
    cuenta_usd = (
        await usuario.post(
            "/api/v1/accounts", {"nombre": "USD", "tipo": "banco", "moneda": "USD"}
        )
    ).json()

    creada = await _gasto(usuario, cuenta_usd, "-100.00")
    assert creada.json()["monto_base"] == "-400000.00"

    editada = await usuario.patch(
        f"/api/v1/transactions/{creada.json()['id']}", {"fecha": "2026-06-25"}
    )
    assert editada.json()["monto_base"] == "-450000.00"


async def test_el_desglose_por_categoria_suma_el_total(
    usuario: Usuario, cuenta: dict, categoria_gasto: dict
):
    otra = (await usuario.get("/api/v1/categories", {"tipo": "gasto"})).json()[1]

    await _gasto(usuario, cuenta, "-300000.00", category_id=categoria_gasto["id"])
    await _gasto(usuario, cuenta, "-100000.00", category_id=otra["id"])
    await _gasto(usuario, cuenta, "-50000.00")  # sin categoria

    desglose = (
        await usuario.get("/api/v1/reports/by-category", {**PERIODO, "tipo": "gasto"})
    ).json()

    assert desglose["total"] == "450000.00"
    assert sum(Decimal(i["total"]) for i in desglose["items"]) == Decimal("450000.00")
    # Lo que no tiene categoria se agrupa, no desaparece del reporte.
    assert "Sin categoria" in [i["nombre"] for i in desglose["items"]]


async def test_el_flujo_de_caja_agrupa_por_mes(usuario: Usuario, cuenta: dict):
    await _ingreso(usuario, cuenta, "5000000.00")
    await _gasto(usuario, cuenta, "-2000000.00")
    await usuario.post(
        "/api/v1/transactions",
        {
            "account_id": cuenta["id"],
            "tipo": "gasto",
            "monto": "-1000000.00",
            "fecha": "2026-07-10",
            "descripcion": "Julio",
        },
    )

    flujo = (
        await usuario.get(
            "/api/v1/reports/cashflow", {"desde": "2026-06-01", "hasta": "2026-07-31"}
        )
    ).json()

    assert len(flujo) == 2
    assert flujo[0]["mes"] == "2026-06-01"
    assert flujo[0]["ingresos"] == "5000000.00"
    assert flujo[0]["gastos"] == "2000000.00"
    assert flujo[0]["neto"] == "3000000.00"
    assert flujo[1]["neto"] == "-1000000.00"


async def test_filtros_y_busqueda_de_texto(usuario: Usuario, cuenta: dict):
    await _gasto(usuario, cuenta, "-10000.00", descripcion="Almuerzo en el centro")
    await _gasto(usuario, cuenta, "-20000.00", descripcion="Gasolina")
    await _ingreso(usuario, cuenta, "30000.00", descripcion="Reembolso")

    solo_gastos = await usuario.get("/api/v1/transactions", {"tipo": "gasto"})
    assert solo_gastos.json()["total"] == 2

    busqueda = await usuario.get("/api/v1/transactions", {"q": "gasolina"})
    assert busqueda.json()["total"] == 1

    fuera_de_rango = await usuario.get(
        "/api/v1/transactions", {"desde": "2026-07-01", "hasta": "2026-07-31"}
    )
    assert fuera_de_rango.json()["total"] == 0
