"""F4.5 · HU-21."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION
from tests.modules.posts.conftest import PUBLICACIONES

MEDIA = Path(get_settings().storage_local_dir)


def test_elimina_de_muro_busqueda_y_lista_del_autor(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    autor = usuario()
    pub = publicar(autor, titulo="Contenido ofensivo", imagenes=2, etiquetas=["x"])
    rutas = [r for (r,) in sql("SELECT ruta_almacenamiento FROM imagen_publicacion")]

    res = moderar(pub["id"], "eliminar", motivo="Lenguaje ofensivo")
    assert res.status_code == 204, res.text
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 0
    assert not any((MEDIA / r).exists() for r in rutas)
    assert client.get(PUBLICACIONES, headers=autor).json()["items"] == []
    assert client.get(PUBLICACIONES, params={"texto": "ofensivo"}).json()["items"] == []
    mias = client.get("/api/v1/usuarios/me/publicaciones", headers=autor).json()
    assert mias["items"] == []
    assert client.get(f"{MODERACION}/publicaciones/{pub['id']}", headers=admin).status_code == 404


def test_eliminar_exige_motivo(client: TestClient, usuario, publicar, moderar) -> None:
    pub = publicar(usuario())
    assert moderar(pub["id"], "eliminar", motivo=" ").status_code == 422
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 1


def test_eliminar_una_oculta(client: TestClient, usuario, publicar, moderar) -> None:
    pub = publicar(usuario())
    moderar(pub["id"], "ocultar", motivo="Spam")
    assert moderar(pub["id"], "eliminar", motivo="Spam reiterado").status_code == 204
    assert sql("SELECT publicacion_id, accion_id FROM moderacion ORDER BY id") == [
        (None, 2),
        (None, 4),
    ]
