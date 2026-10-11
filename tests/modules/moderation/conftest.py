"""Helpers de las pruebas del módulo moderation. Reutiliza usuarios y publicaciones de posts."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.modules.posts.conftest import _clean, publicar, usuario  # noqa: F401

MODERACION = "/api/v1/moderacion"


@pytest.fixture
def admin(usuario) -> dict[str, str]:  # noqa: F811
    return usuario("administrador")


@pytest.fixture
def moderar(client: TestClient, admin: dict[str, str]) -> Callable[..., Any]:
    """Aplica una acción con la marca vigente, como lo haría la pantalla."""

    def _moderar(pub_id: int, accion: str, headers: dict[str, str] | None = None, **cuerpo: Any):
        headers = headers or admin
        if "marca" not in cuerpo:
            cuerpo["marca"] = marca(client, headers, pub_id)
        return client.post(
            f"{MODERACION}/publicaciones/{pub_id}/{accion}", headers=headers, json=cuerpo
        )

    return _moderar


def marca(client: TestClient, headers: dict[str, str], pub_id: int) -> str:
    res = client.get(f"{MODERACION}/publicaciones/{pub_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["marca"]


def bandeja(client: TestClient, headers: dict[str, str], **params: Any) -> list[dict]:
    res = client.get(f"{MODERACION}/publicaciones", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return res.json()["items"]
