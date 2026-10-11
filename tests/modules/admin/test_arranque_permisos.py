"""F5.10 · HU-26, y F1.5 · HU-03: todo /admin es solo para administradores."""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_sessionmaker
from app.modules.identity import service
from tests.helpers import PASSWORD, sql
from tests.modules.admin.conftest import ADMIN

RUTAS = [
    ("get", "/pendientes"),
    ("get", "/solicitudes-profesor"),
    ("get", "/solicitudes-profesor/1"),
    ("post", "/solicitudes-profesor/1/aprobar"),
    ("post", "/solicitudes-profesor/1/rechazar"),
    ("get", "/usuarios"),
    ("get", "/usuarios/1"),
    ("put", "/usuarios/1/rol"),
    ("post", "/usuarios/1/desactivar"),
    ("post", "/usuarios/1/reactivar"),
    ("get", "/categorias"),
    ("post", "/categorias"),
    ("put", "/categorias/1"),
    ("put", "/categorias/1/estado"),
]
CUERPO = {"motivo": "x", "rol_id": 1, "nombre": "Nueva", "activa": False}


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
@pytest.mark.parametrize("rol", ["estudiante", "profesor", None])
def test_solo_administradores(client: TestClient, usuario, metodo, ruta, rol) -> None:
    usuario(correo="victima@facyt.uc.edu.ve")
    headers = usuario(rol) if rol else {}
    kwargs = {"json": CUERPO} if metodo != "get" else {}
    res = getattr(client, metodo)(ADMIN + ruta, headers=headers, **kwargs)
    assert res.status_code == (401 if rol is None else 403)
    assert sql("SELECT count(*) FROM evento_cuenta")[0][0] == 0
    assert sql("SELECT count(*) FROM categoria")[0][0] == 10


def test_admin_inicial_debe_cambiar_la_contrasena(client: TestClient) -> None:
    with get_sessionmaker()() as db:
        service.crear_admin_inicial(
            db, nombre="Administración FaCyT", correo="Admin@FaCyT.uc.edu.ve", password=PASSWORD
        )
    login = client.post(
        "/api/v1/auth/login", json={"correo": "admin@facyt.uc.edu.ve", "password": PASSWORD}
    )
    assert login.json()["debe_cambiar_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = client.get(f"{ADMIN}/usuarios", headers=headers)
    assert res.status_code == 403
    assert res.json()["code"] == "password_change_required"

    client.put(
        "/api/v1/usuarios/me/password",
        headers=headers,
        json={"password_actual": PASSWORD, "password_nueva": "otra-clave-456"},
    )
    assert client.get(f"{ADMIN}/usuarios", headers=headers).status_code == 200


def test_arranca_con_las_diez_categorias_base(client: TestClient) -> None:
    nombres = {c["nombre"] for c in client.get("/api/v1/categorias").json()}
    assert len(nombres) == 10
    assert {"Avisos", "Defensas de TEG", "Investigación"} <= nombres
