# backend-finance

API de finanzas personales: ingresos, gastos, activos y deudas, para responder
una pregunta — **¿cómo voy financieramente en el tiempo?** El eje del producto
es el patrimonio neto histórico, no un simple libro de caja.

**FastAPI** (Python 3.12) · **SQLAlchemy 2.0 async** · **Alembic** ·
**PostgreSQL 16 + pgvector**

El cliente web vive en un repo aparte:
[frontend-finance](https://github.com/Sebastian9726/frontend-finance).

---

## Arranque

Requisitos: Python 3.12 y un PostgreSQL 16 accesible **con la extensión
`pgvector` disponible**.

```bash
cp .env.example .env     # ajustar DATABASE_URL y JWT_SECRET_KEY

py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[ai,dev]"   # Linux/macOS: .venv/bin/python

.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

- API: <http://127.0.0.1:8000>
- Documentación interactiva: <http://127.0.0.1:8000/docs> (deshabilitada en producción)

Comprobar que la cadena de conexión funciona:

```bash
curl http://127.0.0.1:8000/health/db
# {"status":"ok","pgvector":"0.8.6"}
```

### La base de datos

`DATABASE_URL` es el **único acoplamiento** con la base: apúntala a donde
quieras —un contenedor, un Postgres nativo, Neon, RDS— sin tocar una línea de
código. El driver debe ser `asyncpg`; Alembic deriva la versión síncrona
(`psycopg`) por su cuenta.

```bash
DATABASE_URL=postgresql+asyncpg://finance:clave@localhost:5433/finance
DATABASE_URL=postgresql+asyncpg://usuario:clave@host:5432/finance?ssl=require   # gestionado
```

**Si vienes de Prisma**, la cadena no es la misma y las dos diferencias son
obligatorias:

| | |
|---|---|
| Prisma | `postgresql://postgres:admin@localhost:5432/finance?schema=public` |
| SQLAlchemy | `postgresql+asyncpg://postgres:admin@localhost:5432/finance` |

1. **`+asyncpg`** — sin el driver explícito, SQLAlchemy intenta `psycopg2`
   (síncrono) y no arranca.
2. **Sin `?schema=public`** — es exclusivo de Prisma; asyncpg lo pasaría como
   opción del servidor y la conexión falla. No se pierde nada: `public` ya es el
   `search_path` por defecto.

Requisitos de la instancia:

- **pgvector disponible** (`SELECT * FROM pg_available_extensions WHERE
  name='vector'`). La migración `0001` hace `CREATE EXTENSION vector`, lo que
  además exige superusuario o el permiso correspondiente. Un Postgres recién
  instalado **no** la trae y `alembic upgrade head` falla ahí; `/health/db`
  devuelve `"pgvector": null`, que es la forma rápida de detectarlo. La imagen
  `pgvector/pgvector:pg16` ya viene con ella.
- **Una base de datos propia**, no un esquema dentro de otra. Así un error aquí
  no puede tocar datos de otro proyecto en la misma instancia.

Si necesitas levantar una desde cero:

```bash
docker run -d --name finance-db --restart unless-stopped \
  -e POSTGRES_USER=finance -e POSTGRES_PASSWORD=finance_dev_password \
  -e POSTGRES_DB=finance -p 5433:5432 \
  -v finance_pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16
```

Los datos viven en el **volumen**, no en el contenedor: se puede borrar y
recrear el contenedor cuantas veces haga falta mientras se monte el mismo
volumen. `docker volume rm finance_pgdata` sí borra los datos.

---

## Decisiones que conviene conocer antes de tocar el código

### El dinero nunca es `float`

| Capa | Representación |
|---|---|
| Postgres | `NUMERIC(18,2)` montos · `NUMERIC(18,8)` tasas. Nunca `DOUBLE PRECISION` |
| Python | `Decimal` de punta a punta, redondeo explícito `ROUND_HALF_UP` en `quantize_money()` |
| JSON | **string**: `"1234567.89"`, vía `PlainSerializer` en `app/schemas/base.py` |

El string en el JSON no es cosmético: `JSON.parse` convierte todo número a
`double` antes de que el cliente pueda intervenir. Los agregados se calculan
**en SQL**, no sumando filas en Python.

### El `monto` va con signo

Positivo en un ingreso, negativo en un gasto. Así el saldo es literalmente
`saldo_inicial + SUM(monto)` y no hay que recordar qué signo aplicar en cada
consulta. Tres `CHECK` en `transactions` lo imponen; `app/schemas/finance.py`
los replica como validación Pydantic para que el error sea un 422 legible y no
un fallo de integridad de Postgres.

### El saldo de una cuenta NO se guarda

Se calcula con una subconsulta correlacionada (`_saldo_actual_expr()` en
`app/services/accounts.py`). Una columna denormalizada se desincroniza en cuanto
se edita o se borra una transacción, y el error solo aparece meses después.

### La tasa de cambio se congela en la fila

Cada transacción guarda `tasa_a_base` + `monto_base`. Los reportes suman
`monto_base` y **nunca reconvierten**: si se recalculara con la tasa de hoy, el
patrimonio del año pasado cambiaría cada vez que se mueve el dólar.

- Si falta la tasa, la API responde **409 con instrucciones**, no asume 1.
- Si solo hay tasa de un día anterior, se usa y se marca `estimada: true`.
- Editar la **fecha** o la **cuenta** de una transacción **recalcula** la
  conversión (otra fecha = otra tasa; otra cuenta = posiblemente otra moneda).
- El saldo consolidado agrupa **por moneda en SQL** y solo después convierte.

### Aislamiento por usuario desde el día uno

`app/core/tenant.py` expone `owned(modelo, user_id)` y `get_owned_or_404(...)`.
Toda consulta de lista usa el primero; toda mutación que reciba un id usa el
segundo. Se responde **404, no 403**: un 403 confirmaría que ese id existe.

Hay pruebas explícitas por cada router en `tests/test_isolation.py`, incluido el
caso fácil de olvidar: el id ajeno que llega en el **cuerpo** (`account_id`,
`category_id`), no en la URL.

### Toda estructura nueva es una migración

Nunca `Base.metadata.create_all()`, ni en pruebas.

```bash
.venv/Scripts/alembic.exe revision --autogenerate -m "descripcion"
# revisar el archivo generado ANTES de aplicarlo (--autogenerate no detecta renombres)
.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/alembic.exe downgrade -1 && .venv/Scripts/alembic.exe upgrade head
```

La `naming_convention` de `app/db/base.py` es obligatoria: sin ella Alembic no
reconoce los índices y propone recrearlos en cada corrida.

**Gotcha de los ENUM**: `--autogenerate` declara los `sa.Enum` dentro de cada
`create_table`, lo que emite un `CREATE TYPE` por tabla y **falla en la segunda**
que use `currency`; y su `downgrade` no borra los tipos, así que
`downgrade → upgrade` falla con "type already exists". La revisión `0002` está
escrita a mano: crea los ENUM una sola vez con `create_type=False` en las
columnas y los borra al final del downgrade.

### Autenticación

- *Access token* en memoria del cliente (no en `localStorage`), header `Bearer`.
- *Refresh token* en cookie `httpOnly; Secure; SameSite=Lax`; en la base solo se
  guarda su hash SHA-256.
- **Rotación con detección de reúso**: un refresh ya revocado que reaparece
  revoca la **familia** entera (`family_id`), porque no se puede distinguir al
  atacante de la víctima. Cubierto en `tests/test_auth.py`.
- **CORS** con lista blanca desde `CORS_ORIGINS`. Nunca `"*"`: es incompatible
  con `allow_credentials` y deja la API abierta.

---

## Pruebas

```bash
.venv/Scripts/python.exe -m pytest              # 43 pruebas, requiere la BD arriba
.venv/Scripts/ruff.exe check app tests scripts --fix
```

`tests/conftest.py` deriva el nombre de una base aparte reemplazando el último
segmento de `DATABASE_URL` por `finance_test`, y la crea si no existe. Correr
`pytest` contra una instancia compartida añade esa base junto a las demás; es
esperado y no toca la base principal.

**El proxy de Vite disimula el cruce de orígenes**, así que las pruebas de CORS
y de cookie se hacen contra el puerto 8000 directo, no a través del proxy:

```bash
curl -i -X OPTIONS http://127.0.0.1:8000/health \
  -H "Origin: http://localhost:5173" -H "Access-Control-Request-Method: GET"
```

---

## Datos de prueba

```bash
.venv/Scripts/python.exe scripts/seed_demo.py
```

Crea `demo@ejemplo.com` / `demo-finanzas-2026` con 4 cuentas (una en USD), 6
meses de movimientos y tasas de cambio. **Es idempotente**: las tasas usan
`ON CONFLICT DO UPDATE` porque son globales y no se borran al borrar el usuario.

---

## Estado

- [x] **Fase 0** — Andamiaje: Alembic, pgvector, CORS
- [x] **Fase 1** — Auth + cuentas, categorías, transacciones, transferencias, reportes
- [ ] **Fase 2** — Activos, deudas y patrimonio neto en el tiempo
- [ ] **Fase 3** — Presupuestos y metas
- [ ] **Fase 4** — Transacciones recurrentes e importación CSV
- [ ] **Fase 5** — Autocategorización (pgvector) y asistente conversacional
- [ ] **Fase 6** — Despliegue
