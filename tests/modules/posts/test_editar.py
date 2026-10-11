"""F2.7 · HU-11."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.helpers import imagen, sql
from tests.modules.posts.conftest import PUBLICACIONES, archivos, categoria_id, formulario
from tests.modules.posts.test_detalle import ocultar_por_moderacion

MEDIA = Path(get_settings().storage_local_dir)


def _editar(client: TestClient, headers: dict, pub_id: int, orden=(), nuevas=(), **cambios):
    datos = formulario(**cambios)
    datos["orden_imagenes"] = [str(x) for x in orden]
    return client.put(
        f"{PUBLICACIONES}/{pub_id}",
        headers=headers,
        data=datos,
        files=archivos(*nuevas) or None,
    )


def test_editar_cambia_campos_y_marca_editada(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, etiquetas=["viejo", "ia"])
    sql("UPDATE publicacion SET revisada = true")
    res = _editar(
        client,
        autor,
        pub["id"],
        titulo="Defensa movida al aula 7",
        categoria_id=categoria_id("Defensas de TEG"),
        audiencia_id=3,
        etiquetas=["IA", "teg"],
    )
    assert res.status_code == 200, res.text
    editada = res.json()
    assert editada["titulo"] == "Defensa movida al aula 7"
    assert editada["categoria"]["nombre"] == "Defensas de TEG"
    assert editada["audiencia"]["codigo"] == "solo_profesores"
    assert editada["etiquetas"] == ["ia", "teg"]
    assert editada["publicada_en"] == pub["publicada_en"]
    assert editada["editada_en"] is not None
    assert sql("SELECT revisada FROM publicacion")[0][0] is False  # vuelve a "Sin revisar"


def test_solo_el_autor_edita(client: TestClient, usuario, publicar) -> None:
    pub = publicar(usuario())
    for otro in (usuario("profesor"), usuario("administrador")):
        res = _editar(client, otro, pub["id"], titulo="Cambiado por otro")
        assert res.status_code == 403
        assert res.json()["code"] == "author_only"
    assert _editar(client, usuario("estudiante", "x@facyt.uc.edu.ve"), 999999).status_code == 404


def test_oculta_por_moderacion_sigue_oculta_y_revisada(
    client: TestClient, usuario, publicar
) -> None:
    autor = usuario()
    usuario("administrador")
    pub = publicar(autor)
    ocultar_por_moderacion(pub["id"])
    res = _editar(client, autor, pub["id"], titulo="Corregida")
    assert res.status_code == 200
    assert sql("SELECT estado_id, revisada FROM publicacion")[0] == (3, True)


def test_categoria_desactivada_se_conserva_al_editar(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, categoria_id=categoria_id("Logros"))
    sql("UPDATE categoria SET activa = false WHERE nombre = 'Logros'")
    res = _editar(client, autor, pub["id"], categoria_id=categoria_id("Logros"), titulo="Otro")
    assert res.status_code == 200
    # Pero no se puede pasar a una categoría desactivada.
    otra = publicar(autor)
    res = _editar(client, autor, otra["id"], categoria_id=categoria_id("Logros"))
    assert res.status_code == 422


def test_reordenar_quitar_y_agregar_imagenes(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, imagenes=3)
    a, b, c = (img["id"] for img in pub["imagenes"])
    ruta_b = sql("SELECT ruta_almacenamiento FROM imagen_publicacion WHERE id = :id", id=b)[0][0]

    # Nueva portada, se quita b, c pasa al final.
    res = _editar(client, autor, pub["id"], orden=["nueva:0", a, c], nuevas=[imagen("JPEG")])
    assert res.status_code == 200, res.text
    imagenes = res.json()["imagenes"]
    assert [i["orden"] for i in imagenes] == [1, 2, 3]
    assert [i["id"] for i in imagenes][1:] == [a, c]
    assert imagenes[0]["id"] not in (a, b, c)
    assert not (MEDIA / ruta_b).exists()  # se encoló y la cola se vació tras el commit
    assert sql("SELECT count(*) FROM archivo_por_borrar")[0][0] == 0

    # Intercambiar posiciones (UNIQUE diferible).
    res = _editar(client, autor, pub["id"], orden=[c, a])
    assert [i["id"] for i in res.json()["imagenes"]] == [c, a]


def test_orden_de_imagenes_invalido(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, imagenes=1)
    ajena = publicar(usuario("profesor"), imagenes=1)["imagenes"][0]["id"]
    propia = pub["imagenes"][0]["id"]
    casos = [
        ({"orden": [ajena]}, "orden_imagenes.0"),
        ({"orden": [propia, propia]}, "orden_imagenes.1"),
        ({"orden": ["nueva:1"], "nuevas": [imagen()]}, "orden_imagenes.0"),
        ({"orden": [propia], "nuevas": [imagen()]}, "orden_imagenes"),
        (
            {"orden": [propia, *[f"nueva:{i}" for i in range(5)]], "nuevas": [imagen()] * 5},
            "imagenes",
        ),
    ]
    for kwargs, campo in casos:
        res = _editar(client, autor, pub["id"], **kwargs)
        assert res.status_code == 422, kwargs
        assert [e["field"] for e in res.json()["errors"]] == [campo]
    assert (
        sql("SELECT count(*) FROM imagen_publicacion WHERE publicacion_id = :id", id=pub["id"])[0][
            0
        ]
        == 1
    )
