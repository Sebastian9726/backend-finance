"""Patrimonio neto en el tiempo.

Lo que se prueba aqui es que la serie mensual diga la verdad: que consolide
monedas distintas con la tasa de CADA cierre, que corregir una valuacion vieja
arrastre los meses siguientes, y que los centavos lleguen intactos.
"""

from datetime import date

from tests.conftest import Usuario

# Fechas fijas en el pasado: si dependieran de `date.today()` la prueba
# cambiaria de significado al pasar de mes.
ENERO = "2026-01-31"
FEBRERO = "2026-02-28"
MARZO = "2026-03-31"

RANGO = {"desde": "2026-01-01", "hasta": "2026-12-31"}


async def _tasa(usuario: Usuario, fecha: str, valor: str) -> None:
    """Deja registrada la tasa USD -> COP de un dia."""
    response = await usuario.post(
        "/api/v1/exchange-rates",
        {"fecha": fecha, "origen": "USD", "destino": "COP", "tasa": valor},
    )
    assert response.status_code == 201, response.text


async def _serie(usuario: Usuario) -> list[dict]:
    response = await usuario.get("/api/v1/networth/series", RANGO)
    assert response.status_code == 200, response.text
    return response.json()["puntos"]


def _punto(puntos: list[dict], fecha: str) -> dict:
    encontrado = next((p for p in puntos if p["fecha"] == fecha), None)
    assert encontrado is not None, f"no hay snapshot del {fecha} en {[p['fecha'] for p in puntos]}"
    return encontrado


# ---------------------------------------------------------------------------
# Activos
# ---------------------------------------------------------------------------


async def test_crear_activo_deja_su_primera_valuacion(usuario: Usuario):
    """Un activo nunca existe sin valor: el POST crea tambien la valuacion."""
    response = await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Apartamento",
            "tipo": "inmueble",
            "moneda": "COP",
            "valor_actual": "350000000.00",
            "fecha_valor": ENERO,
            "costo_adquisicion": "300000000.00",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["valor_actual"] == "350000000.00"
    assert body["fecha_valor"] == ENERO
    # Ganancia contra el costo de adquisicion.
    assert body["ganancia"] == "50000000.00"
    assert len(body["valuaciones"]) == 1


async def test_activo_sin_costo_no_reporta_ganancia(usuario: Usuario):
    """Sin punto de partida no hay ganancia que calcular: `null`, no cero."""
    response = await usuario.post(
        "/api/v1/assets",
        {"nombre": "Herencia", "tipo": "otro", "moneda": "COP", "valor_actual": "1000000.00"},
    )
    assert response.json()["ganancia"] is None


async def test_los_centavos_del_activo_sobreviven_el_viaje(usuario: Usuario):
    """La verificacion de plata de siempre, ahora sobre patrimonio."""
    creado = await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Inversion",
            "tipo": "inversion",
            "moneda": "COP",
            "valor_actual": "1234567.89",
            "fecha_valor": ENERO,
        },
    )
    assert creado.json()["valor_actual"] == "1234567.89"

    puntos = await _serie(usuario)
    assert _punto(puntos, ENERO)["patrimonio_neto"] == "1234567.89"


# ---------------------------------------------------------------------------
# La serie
# ---------------------------------------------------------------------------


async def test_serie_consolida_cop_y_usd_con_la_tasa_de_cada_cierre(usuario: Usuario):
    """El caso que da sentido a materializar la serie.

    Un activo en pesos y otro en dolares, con el dolar moviendose entre enero y
    febrero. Cada mes debe convertir con SU tasa: si se reconvirtiera todo con
    la tasa de hoy, enero cambiaria cada vez que se mueve el dolar.
    """
    await _tasa(usuario, ENERO, "4000.00000000")
    await _tasa(usuario, FEBRERO, "4500.00000000")

    await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Ahorro COP",
            "tipo": "ahorro",
            "moneda": "COP",
            "valor_actual": "10000000.00",
            "fecha_valor": ENERO,
        },
    )
    await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Cuenta USD",
            "tipo": "ahorro",
            "moneda": "USD",
            "valor_actual": "1000.00",
            "fecha_valor": ENERO,
        },
    )

    puntos = await _serie(usuario)

    # Enero: 10.000.000 + (1.000 USD x 4.000) = 14.000.000
    assert _punto(puntos, ENERO)["total_activos"] == "14000000.00"
    # Febrero: el mismo USD vale mas porque el dolar subio.
    # 10.000.000 + (1.000 USD x 4.500) = 14.500.000
    assert _punto(puntos, FEBRERO)["total_activos"] == "14500000.00"
    # Y enero NO se movio al calcularse febrero.
    assert _punto(puntos, ENERO)["total_activos"] == "14000000.00"


async def test_las_deudas_restan(usuario: Usuario):
    await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Casa",
            "tipo": "inmueble",
            "moneda": "COP",
            "valor_actual": "400000000.00",
            "fecha_valor": ENERO,
        },
    )
    creada = await usuario.post(
        "/api/v1/liabilities",
        {
            "nombre": "Hipoteca",
            "tipo": "hipoteca",
            "moneda": "COP",
            "saldo_actual": "250000000.00",
            "fecha_saldo": ENERO,
            "principal": "300000000.00",
        },
    )
    assert creada.status_code == 201, creada.text
    # El saldo se guarda POSITIVO y `abonado` sale del principal.
    assert creada.json()["saldo_actual"] == "250000000.00"
    assert creada.json()["abonado"] == "50000000.00"

    enero = _punto(await _serie(usuario), ENERO)
    assert enero["total_activos"] == "400000000.00"
    assert enero["total_pasivos"] == "250000000.00"
    assert enero["patrimonio_neto"] == "150000000.00"


async def test_corregir_una_valuacion_vieja_arrastra_los_meses_siguientes(usuario: Usuario):
    """La prueba que exige el plan: cambiar el pasado recalcula el futuro.

    Sin el recompute desde la fecha afectada, febrero y marzo se quedarian con
    el valor viejo y la grafica mostraria un escalon que no existe.
    """
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {
                "nombre": "Lote",
                "tipo": "inmueble",
                "moneda": "COP",
                "valor_actual": "100000000.00",
                "fecha_valor": ENERO,
            },
        )
    ).json()

    # Marzo sube de precio.
    await usuario.post(
        f"/api/v1/assets/{activo['id']}/valuations",
        {"fecha": MARZO, "valor": "120000000.00"},
    )

    puntos = await _serie(usuario)
    assert _punto(puntos, ENERO)["patrimonio_neto"] == "100000000.00"
    assert _punto(puntos, FEBRERO)["patrimonio_neto"] == "100000000.00"
    assert _punto(puntos, MARZO)["patrimonio_neto"] == "120000000.00"

    # Ahora se corrige ENERO: el valor inicial estaba mal.
    await usuario.post(
        f"/api/v1/assets/{activo['id']}/valuations",
        {"fecha": ENERO, "valor": "90000000.00"},
    )

    puntos = await _serie(usuario)
    assert _punto(puntos, ENERO)["patrimonio_neto"] == "90000000.00"
    # Febrero arrastra el valor corregido de enero...
    assert _punto(puntos, FEBRERO)["patrimonio_neto"] == "90000000.00"
    # ...pero marzo conserva su propia valuacion, que no se toco.
    assert _punto(puntos, MARZO)["patrimonio_neto"] == "120000000.00"


async def test_revaluar_la_misma_fecha_corrige_en_vez_de_duplicar(usuario: Usuario):
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {
                "nombre": "Carro",
                "tipo": "vehiculo",
                "moneda": "COP",
                "valor_actual": "50000000.00",
                "fecha_valor": ENERO,
            },
        )
    ).json()

    await usuario.post(
        f"/api/v1/assets/{activo['id']}/valuations",
        {"fecha": ENERO, "valor": "48000000.00"},
    )

    detalle = (await usuario.get(f"/api/v1/assets/{activo['id']}")).json()
    assert len(detalle["valuaciones"]) == 1
    assert detalle["valor_actual"] == "48000000.00"


async def test_vender_se_registra_como_valuacion_en_cero(usuario: Usuario):
    """Un carro vendido en marzo seguia siendo patrimonio en enero.

    Por eso el flag `activo` no se aplica hacia atras: la forma correcta de
    dejar de contarlo es una valuacion en 0 el dia de la venta.
    """
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {
                "nombre": "Moto",
                "tipo": "vehiculo",
                "moneda": "COP",
                "valor_actual": "15000000.00",
                "fecha_valor": ENERO,
            },
        )
    ).json()

    await usuario.post(
        f"/api/v1/assets/{activo['id']}/valuations",
        {"fecha": MARZO, "valor": "0.00", "nota": "Vendida"},
    )
    await usuario.patch(f"/api/v1/assets/{activo['id']}", {"activo": False})

    puntos = await _serie(usuario)
    assert _punto(puntos, ENERO)["patrimonio_neto"] == "15000000.00"
    assert _punto(puntos, FEBRERO)["patrimonio_neto"] == "15000000.00"
    assert _punto(puntos, MARZO)["patrimonio_neto"] == "0.00"


async def test_la_serie_reporta_la_variacion_del_periodo(usuario: Usuario):
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {
                "nombre": "Portafolio",
                "tipo": "inversion",
                "moneda": "COP",
                "valor_actual": "10000000.00",
                "fecha_valor": ENERO,
            },
        )
    ).json()
    await usuario.post(
        f"/api/v1/assets/{activo['id']}/valuations",
        {"fecha": MARZO, "valor": "12000000.00"},
    )

    serie = (await usuario.get("/api/v1/networth/series", RANGO)).json()
    assert serie["variacion"] == "2000000.00"
    assert serie["variacion_pct"] == 20.0
    assert serie["moneda_base"] == "COP"


async def test_sin_activos_ni_deudas_la_serie_va_vacia(usuario: Usuario):
    serie = (await usuario.get("/api/v1/networth/series", RANGO)).json()
    assert serie["puntos"] == []
    assert serie["variacion"] == "0.00"
    assert serie["variacion_pct"] is None


async def test_borrar_el_activo_lo_saca_de_toda_la_historia(usuario: Usuario):
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {
                "nombre": "Temporal",
                "tipo": "otro",
                "moneda": "COP",
                "valor_actual": "5000000.00",
                "fecha_valor": ENERO,
            },
        )
    ).json()
    assert len(await _serie(usuario)) > 0

    assert (await usuario.delete(f"/api/v1/assets/{activo['id']}")).status_code == 204
    assert await _serie(usuario) == []


# ---------------------------------------------------------------------------
# Composicion
# ---------------------------------------------------------------------------


async def test_composicion_desglosa_y_convierte_a_moneda_base(usuario: Usuario):
    hoy = date.today().isoformat()
    await _tasa(usuario, hoy, "4000.00000000")

    await usuario.post(
        "/api/v1/assets",
        {"nombre": "Ahorro COP", "tipo": "ahorro", "moneda": "COP", "valor_actual": "6000000.00"},
    )
    await usuario.post(
        "/api/v1/assets",
        {"nombre": "Cuenta USD", "tipo": "ahorro", "moneda": "USD", "valor_actual": "500.00"},
    )
    await usuario.post(
        "/api/v1/liabilities",
        {"nombre": "Tarjeta", "tipo": "tarjeta", "moneda": "COP", "saldo_actual": "2000000.00"},
    )

    comp = (await usuario.get("/api/v1/networth/composition")).json()

    # 6.000.000 + (500 USD x 4.000 = 2.000.000) = 8.000.000
    assert comp["total_activos"] == "8000000.00"
    assert comp["total_pasivos"] == "2000000.00"
    assert comp["patrimonio_neto"] == "6000000.00"

    # Ordenado de mayor a menor, con el valor original y el convertido.
    assert [a["nombre"] for a in comp["activos"]] == ["Ahorro COP", "Cuenta USD"]
    usd = comp["activos"][1]
    assert usd["valor_original"] == "500.00"
    assert usd["valor_base"] == "2000000.00"
    assert usd["porcentaje"] == 25.0


# ---------------------------------------------------------------------------
# Tasas faltantes
# ---------------------------------------------------------------------------


async def test_sin_ninguna_tasa_un_activo_en_usd_falla_con_instrucciones(usuario: Usuario):
    """No se inventa una tasa: se falla diciendo que hace falta cargarla."""
    response = await usuario.post(
        "/api/v1/assets",
        {"nombre": "Cuenta USD", "tipo": "ahorro", "moneda": "USD", "valor_actual": "1000.00"},
    )
    assert response.status_code == 409
    assert "tasa de cambio" in response.json()["detail"]


async def test_una_tasa_posterior_sirve_de_ultimo_recurso_y_queda_marcada(usuario: Usuario):
    """Quien carga tasas desde marzo y registra un activo de enero no deberia
    perder la serie entera: se usa la tasa real mas antigua y se marca."""
    await _tasa(usuario, MARZO, "4500.00000000")

    await usuario.post(
        "/api/v1/assets",
        {
            "nombre": "Cuenta USD",
            "tipo": "ahorro",
            "moneda": "USD",
            "valor_actual": "1000.00",
            "fecha_valor": ENERO,
        },
    )

    puntos = await _serie(usuario)
    enero = _punto(puntos, ENERO)
    # Se uso la tasa de marzo por no haber ninguna anterior, y se advierte.
    assert enero["total_activos"] == "4500000.00"
    assert enero["tasa_estimada"] is True
    # Marzo si tiene su tasa propia: no se marca.
    assert _punto(puntos, MARZO)["tasa_estimada"] is False


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------


async def test_no_se_borra_la_unica_valuacion(usuario: Usuario):
    activo = (
        await usuario.post(
            "/api/v1/assets",
            {"nombre": "Unico", "tipo": "otro", "moneda": "COP", "valor_actual": "100.00"},
        )
    ).json()
    valuacion = activo["valuaciones"][0]

    response = await usuario.delete(
        f"/api/v1/assets/{activo['id']}/valuations/{valuacion['id']}"
    )
    assert response.status_code == 409
    assert "unica valuacion" in response.json()["detail"]


async def test_nombre_de_activo_repetido_da_409(usuario: Usuario):
    datos = {"nombre": "Casa", "tipo": "inmueble", "moneda": "COP", "valor_actual": "1.00"}
    assert (await usuario.post("/api/v1/assets", datos)).status_code == 201
    repetido = await usuario.post("/api/v1/assets", datos)
    assert repetido.status_code == 409


async def test_la_deuda_no_acepta_fin_anterior_al_inicio(usuario: Usuario):
    response = await usuario.post(
        "/api/v1/liabilities",
        {
            "nombre": "Mal fechada",
            "tipo": "personal",
            "moneda": "COP",
            "saldo_actual": "100.00",
            "fecha_inicio": "2026-06-01",
            "fecha_fin": "2026-01-01",
        },
    )
    assert response.status_code == 422
