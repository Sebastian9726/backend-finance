"""Autenticacion: registro, login y rotacion de tokens."""

from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import PASSWORD, Usuario, registrar

COOKIE = settings.refresh_cookie_name


async def test_registro_devuelve_tokens_y_siembra_categorias(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "nueva@ejemplo.com", "password": PASSWORD, "nombre": "Nueva"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "nueva@ejemplo.com"

    categorias = await client.get(
        "/api/v1/categories", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert len(categorias.json()) > 10


async def test_refresh_token_va_en_cookie_httponly(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "cookie@ejemplo.com", "password": PASSWORD, "nombre": "Cookie"},
    )
    # El refresh token no debe aparecer en el cuerpo: si el JavaScript de la
    # pagina pudiera leerlo, un XSS se llevaria la sesion persistente.
    assert "refresh" not in response.text.lower()

    set_cookie = response.headers["set-cookie"]
    assert COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie


async def test_email_duplicado_da_409(client: AsyncClient):
    datos = {"email": "repe@ejemplo.com", "password": PASSWORD, "nombre": "Repe"}
    assert (await client.post("/api/v1/auth/register", json=datos)).status_code == 201
    assert (await client.post("/api/v1/auth/register", json=datos)).status_code == 409


async def test_login_correcto_e_incorrecto(client: AsyncClient, usuario: Usuario):
    ok = await client.post(
        "/api/v1/auth/login", json={"email": usuario.email, "password": PASSWORD}
    )
    assert ok.status_code == 200

    malo = await client.post(
        "/api/v1/auth/login", json={"email": usuario.email, "password": "otra-cosa-123"}
    )
    assert malo.status_code == 401

    inexistente = await client.post(
        "/api/v1/auth/login", json={"email": "nadie@ejemplo.com", "password": PASSWORD}
    )
    # Mismo mensaje que la contrasena incorrecta: distinguirlos convertiria el
    # login en un detector de cuentas registradas.
    assert inexistente.status_code == 401
    assert inexistente.json()["detail"] == malo.json()["detail"]


async def test_me_exige_token_valido(client: AsyncClient, usuario: Usuario):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer basura"})
    ).status_code == 401

    yo = await usuario.get("/api/v1/auth/me")
    assert yo.status_code == 200
    assert yo.json()["email"] == usuario.email


async def test_refresh_rota_el_token(client: AsyncClient):
    await registrar(client, "rota@ejemplo.com")
    primero = client.cookies[COOKIE]

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert response.json()["access_token"]

    segundo = client.cookies[COOKIE]
    assert segundo != primero, "cada refresh debe entregar un token nuevo"


async def test_reusar_un_refresh_revocado_invalida_toda_la_familia(client: AsyncClient):
    """La prueba de seguridad central de la rotacion.

    Un token ya rotado que vuelve a presentarse significa que alguien guardo
    una copia. Como no se puede saber si quien lo presenta es el atacante o la
    victima, se revoca la cadena completa y ambos deben autenticarse de nuevo.
    """
    await registrar(client, "robo@ejemplo.com")
    robado = client.cookies[COOKIE]

    # El usuario legitimo refresca: el token robado queda revocado.
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    vigente = client.cookies[COOKIE]

    # El atacante intenta usar su copia.
    client.cookies.set(COOKIE, robado)
    reuso = await client.post("/api/v1/auth/refresh")
    assert reuso.status_code == 401

    # Y el token que SI era valido tambien queda muerto.
    client.cookies.set(COOKIE, vigente)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_logout_invalida_la_sesion(client: AsyncClient):
    await registrar(client, "salida@ejemplo.com")
    token = client.cookies[COOKIE]

    assert (await client.post("/api/v1/auth/logout")).status_code == 204

    client.cookies.set(COOKIE, token)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_refresh_sin_cookie_da_401(client: AsyncClient):
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_contrasena_debe_ser_razonable(client: AsyncClient):
    for mala in ["corta", "1234567890123", "solamenteletras"]:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": f"{len(mala)}@ejemplo.com", "password": mala, "nombre": "X"},
        )
        assert response.status_code == 422, f"acepto la contrasena '{mala}'"
