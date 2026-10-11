"""Helpers de las pruebas del Módulo 5 (administración). Reutiliza usuarios y publicaciones."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.posts.conftest import _clean, publicar, usuario  # noqa: F401

ADMIN = "/api/v1/admin"


@pytest.fixture
def admin(usuario) -> dict[str, str]:  # noqa: F811
    return usuario("administrador")


def id_de(correo: str) -> int:
    return sql("SELECT id FROM usuario WHERE correo = :c", c=correo)[0][0]


def pedir_profesor(client: TestClient, headers: dict[str, str]) -> None:
    res = client.post("/api/v1/usuarios/me/solicitud-profesor", headers=headers)
    assert res.status_code == 201, res.text


def solicitud_pendiente(correo: str) -> int:
    return sql(
        "SELECT s.id FROM solicitud_profesor s JOIN usuario u ON u.id = s.usuario_id "
        "WHERE u.correo = :c AND s.estado_id = 1",
        c=correo,
    )[0][0]


def get(client: TestClient, headers: dict[str, str], ruta: str, **params: Any) -> Any:
    res = client.get(f"{ADMIN}{ruta}", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return res.json()
