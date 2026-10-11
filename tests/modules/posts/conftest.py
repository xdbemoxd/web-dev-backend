"""Helpers de las pruebas del módulo posts: usuarios por rol y publicaciones listas."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.helpers import imagen, sql

PUBLICACIONES = "/api/v1/publicaciones"
ROLES = {"estudiante": 1, "profesor": 2, "administrador": 3}


@pytest.fixture(autouse=True)
def _clean(clean_db: None) -> Iterator[None]:
    yield
    # Algunas pruebas desactivan categorías; los catálogos no los vacía clean_db.
    sql("UPDATE categoria SET activa = true")


@pytest.fixture
def usuario(registrar, login) -> Callable[..., dict[str, str]]:
    """Crea una cuenta con el rol pedido y devuelve su header Authorization."""

    def _usuario(rol: str = "estudiante", correo: str | None = None) -> dict[str, str]:
        correo = correo or f"{rol}@facyt.uc.edu.ve"
        registrar(correo)
        if rol != "estudiante":
            sql("UPDATE usuario SET rol_id = :rol WHERE correo = :c", rol=ROLES[rol], c=correo)
        return login(correo)

    return _usuario


def categoria_id(nombre: str = "Avisos") -> int:
    return sql("SELECT id FROM categoria WHERE nombre = :n", n=nombre)[0][0]


def formulario(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "titulo": "Defensa de TEG",
        "contenido": "El jueves a las 10 am en el aula 5.",
        "categoria_id": categoria_id(),
    }
    datos.update(cambios)
    return datos


def archivos(*contenidos: bytes) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("imagenes", (f"foto{i}.png", c, "image/png")) for i, c in enumerate(contenidos)]


@pytest.fixture
def publicar(client: TestClient) -> Callable[..., dict]:
    def _publicar(headers: dict[str, str], imagenes: int = 0, **cambios: Any) -> dict:
        res = client.post(
            PUBLICACIONES,
            headers=headers,
            data=formulario(**cambios),
            files=archivos(*[imagen() for _ in range(imagenes)]) or None,
        )
        assert res.status_code == 201, res.text
        return res.json()

    return _publicar
