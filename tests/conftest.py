"""Fixtures comunes. Los tests usan una BD aparte (<db>_test) que se crea y migra por sesión."""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, make_url, text

# Debe configurarse ANTES de importar la app (get_settings() se cachea).
_base_url = make_url(
    os.environ.get("DATABASE_URL", "postgresql+psycopg://redfacyt:redfacyt@localhost:5432/redfacyt")
).set(drivername="postgresql+psycopg")
_test_url = _base_url.set(database=f"{_base_url.database}_test")
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
# Redis de pruebas en la base lógica 15 para no pisar la cache de desarrollo.
_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
os.environ["REDIS_URL"] = _redis_url.rsplit("/", 1)[0] + "/15"
os.environ["APP_ENV"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_DIR"] = tempfile.mkdtemp(prefix="redfacyt-media-")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.helpers import PASSWORD  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _recreate_test_database() -> None:
    admin = create_engine(_base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_test_url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{_test_url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    _recreate_test_database()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # https: el navegador (y httpx) solo envía la cookie Secure del refresh token por HTTPS.
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c


# Tablas con datos de negocio. Los catálogos (migración 0003) no se tocan.
_TABLAS_DE_NEGOCIO = (
    "moderacion, archivo_por_borrar, publicacion_etiqueta, etiqueta, imagen_publicacion, "
    "publicacion, refresh_token, sesion, evento_cuenta, solicitud_profesor, usuario"
)


@pytest.fixture
def clean_db() -> Iterator[None]:
    """Vacía las tablas de negocio después de la prueba.

    Los historiales y `usuario` tienen triggers que prohíben borrar o vaciar; en la base de
    pruebas se desactivan solo para esta limpieza (session_replication_role = replica).
    """
    yield
    with get_engine().begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text(f"TRUNCATE {_TABLAS_DE_NEGOCIO} RESTART IDENTITY"))


@pytest.fixture
def registrar(client: TestClient) -> Callable[..., dict]:
    def _registrar(correo: str = "ana@facyt.uc.edu.ve", **extra: Any) -> dict:
        body = {"nombre_completo": "Ana Pérez", "correo": correo, "password": PASSWORD, **extra}
        res = client.post("/api/v1/auth/registro", json=body)
        assert res.status_code == 201, res.text
        return res.json()

    return _registrar


@pytest.fixture
def login(client: TestClient) -> Callable[..., dict[str, str]]:
    """Inicia sesión y devuelve el header Authorization. La cookie queda en el cliente."""

    def _login(correo: str = "ana@facyt.uc.edu.ve", password: str = PASSWORD) -> dict[str, str]:
        res = client.post("/api/v1/auth/login", json={"correo": correo, "password": password})
        assert res.status_code == 200, res.text
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    return _login
