"""F1.5 · HU-03, HU-22: la moderación es solo para administradores, en el servidor."""

import pytest
from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION

RUTAS = [
    ("get", "/publicaciones"),
    ("get", "/publicaciones/pendientes"),
    ("get", "/publicaciones/{id}"),
    ("post", "/publicaciones/{id}/revisar"),
    ("post", "/publicaciones/{id}/ocultar"),
    ("post", "/publicaciones/{id}/restaurar"),
    ("post", "/publicaciones/{id}/eliminar"),
    ("post", "/publicaciones/revisar-lote"),
    ("get", "/historial"),
    ("get", "/moderadores"),
]


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS)
@pytest.mark.parametrize("rol", ["estudiante", "profesor", None])
def test_solo_administradores(client: TestClient, usuario, publicar, metodo, ruta, rol) -> None:
    pub = publicar(usuario(correo="autor@facyt.uc.edu.ve"))
    headers = usuario(rol) if rol else {}
    kwargs = {"json": {"marca": "x", "motivo": "x", "items": [{"id": 1, "marca": "x"}]}}
    res = getattr(client, metodo)(
        MODERACION + ruta.format(id=pub["id"]),
        headers=headers,
        **(kwargs if metodo == "post" else {}),
    )
    assert res.status_code == (401 if rol is None else 403)
    assert sql("SELECT estado_id, revisada FROM publicacion")[0] == (1, False)
    assert sql("SELECT count(*) FROM moderacion")[0][0] == 0
