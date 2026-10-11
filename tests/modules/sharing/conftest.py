"""Helpers de las pruebas del Módulo 6 (tarjeta para Instagram)."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.modules.posts.conftest import _clean, publicar, usuario  # noqa: F401

PUBLICA, COMUNIDAD, SOLO_PROFESORES = 1, 2, 3


@pytest.fixture
def admin(usuario) -> dict[str, str]:  # noqa: F811
    return usuario("administrador")


def tarjeta(client: TestClient, headers: dict[str, str], pub_id: int, **params):
    return client.get(f"/api/v1/publicaciones/{pub_id}/tarjeta", headers=headers, params=params)


def abrir(contenido: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(contenido))
    img.load()
    return img
