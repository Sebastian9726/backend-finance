FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Extras a instalar. En produccion: --build-arg EXTRAS=ai (sin dev).
ARG EXTRAS=ai,dev

# Las dependencias se instalan antes de copiar el codigo para aprovechar la
# cache de capas. El paquete `app` se crea vacio porque setuptools lo busca.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --upgrade pip \
    && pip install ".[${EXTRAS}]"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
