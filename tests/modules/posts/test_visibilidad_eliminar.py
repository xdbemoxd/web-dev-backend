"""F2.8, F2.9 · HU-12."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.helpers import sql
from tests.modules.posts.conftest import PUBLICACIONES
from tests.modules.posts.test_detalle import ocultar_por_moderacion

MEDIA = Path(get_settings().storage_local_dir)


def _visibilidad(client: TestClient, headers: dict, pub_id: int, visible: bool):
    return client.put(
        f"{PUBLICACIONES}/{pub_id}/visibilidad", headers=headers, json={"visible": visible}
    )


def test_ocultar_y_mostrar(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor)
    res = _visibilidad(client, autor, pub["id"], False)
    assert res.status_code == 200
    assert res.json()["estado"]["codigo"] == "oculta_autor"
    assert _visibilidad(client, autor, pub["id"], False).status_code == 200  # idempotente
    res = _visibilidad(client, autor, pub["id"], True)
    assert res.json()["estado"]["codigo"] == "visible"
    assert res.json()["publicada_en"] == pub["publicada_en"]  # vuelve en su fecha original


def test_no_se_muestra_lo_que_oculto_un_admin(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    usuario("administrador")
    pub = publicar(autor)
    ocultar_por_moderacion(pub["id"], "Información incorrecta")
    for visible in (True, False):
        res = _visibilidad(client, autor, pub["id"], visible)
        assert res.status_code == 409
        assert res.json()["code"] == "hidden_by_moderation"
        assert "Información incorrecta" in res.json()["detail"]
    assert sql("SELECT estado_id FROM publicacion")[0][0] == 3


def test_solo_el_autor_oculta_o_elimina(client: TestClient, usuario, publicar) -> None:
    pub = publicar(usuario())
    admin = usuario("administrador")
    assert _visibilidad(client, admin, pub["id"], False).status_code == 403
    assert client.delete(f"{PUBLICACIONES}/{pub['id']}", headers=admin).status_code == 403
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 1


def test_eliminar_borra_imagenes_y_conserva_historial(
    client: TestClient, usuario, publicar
) -> None:
    autor = usuario()
    usuario("administrador")
    pub = publicar(autor, imagenes=2, etiquetas=["ia"])
    rutas = [r for (r,) in sql("SELECT ruta_almacenamiento FROM imagen_publicacion")]
    assert all((MEDIA / r).exists() for r in rutas)
    ocultar_por_moderacion(pub["id"])

    res = client.delete(f"{PUBLICACIONES}/{pub['id']}", headers=autor)
    assert res.status_code == 204
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 0
    assert sql("SELECT count(*) FROM imagen_publicacion")[0][0] == 0
    assert sql("SELECT count(*) FROM archivo_por_borrar")[0][0] == 0
    assert not any((MEDIA / r).exists() for r in rutas)
    # El historial de moderación sobrevive sin la publicación (F4.6).
    assert sql("SELECT publicacion_id, titulo_al_momento FROM moderacion")[0] == (
        None,
        "Defensa de TEG",
    )
    assert client.get(f"{PUBLICACIONES}/{pub['id']}", headers=autor).status_code == 404
    assert client.delete(f"{PUBLICACIONES}/{pub['id']}", headers=autor).status_code == 404
