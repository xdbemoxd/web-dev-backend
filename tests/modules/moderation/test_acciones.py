"""F4.3, F4.4, F2.8 · HU-20."""

import pytest
from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.posts.conftest import PUBLICACIONES


def _muro(client: TestClient, **params) -> list[int]:
    return [i["id"] for i in client.get(PUBLICACIONES, params=params).json()["items"]]


@pytest.mark.parametrize("motivo", [None, "", "   "])
def test_ocultar_exige_motivo(client: TestClient, usuario, publicar, moderar, motivo) -> None:
    pub = publicar(usuario())
    cuerpo = {} if motivo is None else {"motivo": motivo}
    res = moderar(pub["id"], "ocultar", **cuerpo)
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "motivo"
    assert sql("SELECT estado_id FROM publicacion")[0][0] == 1
    assert sql("SELECT count(*) FROM moderacion")[0][0] == 0


def test_oculta_no_aparece_en_muro_ni_busqueda(
    client: TestClient, usuario, publicar, moderar
) -> None:
    autor = usuario()
    pub = publicar(autor, titulo="Rifa de rectorado", audiencia_id=1)
    assert _muro(client, texto="rifa") == [pub["id"]]
    res = moderar(pub["id"], "ocultar", motivo="  Ajeno a la facultad  ")
    assert res.status_code == 200, res.text
    assert res.json()["publicacion"]["estado"]["codigo"] == "oculta_moderacion"
    assert _muro(client) == []
    assert _muro(client, texto="rifa") == []
    lector = usuario(correo="lector@facyt.uc.edu.ve")
    assert client.get(f"{PUBLICACIONES}/{pub['id']}", headers=lector).status_code == 403

    # El autor la ve con el motivo y no puede volver a mostrarla (F2.8).
    propia = client.get(f"{PUBLICACIONES}/{pub['id']}", headers=autor).json()
    assert propia["moderacion"]["motivo"] == "Ajeno a la facultad"
    res = client.put(
        f"{PUBLICACIONES}/{pub['id']}/visibilidad", headers=autor, json={"visible": True}
    )
    assert res.status_code == 409


def test_ocultar_una_oculta_por_el_autor(client: TestClient, usuario, publicar, moderar) -> None:
    autor = usuario()
    pub = publicar(autor)
    client.put(f"{PUBLICACIONES}/{pub['id']}/visibilidad", headers=autor, json={"visible": False})
    assert moderar(pub["id"], "ocultar", motivo="Spam").status_code == 200
    assert sql("SELECT estado_id, revisada FROM publicacion")[0] == (3, True)


def test_restaurar_vuelve_a_su_posicion(client: TestClient, usuario, publicar, moderar) -> None:
    autor = usuario()
    vieja = publicar(autor, titulo="Vieja")
    nueva = publicar(autor, titulo="Nueva")
    moderar(vieja["id"], "ocultar", motivo="Falta el aula")
    ids = [i["id"] for i in client.get(PUBLICACIONES, headers=autor).json()["items"]]
    assert ids == [nueva["id"]]
    res = moderar(vieja["id"], "restaurar")
    assert res.status_code == 200, res.text
    cuerpo = res.json()
    assert cuerpo["publicacion"]["estado"]["codigo"] == "visible"
    assert cuerpo["publicacion"]["publicada_en"] == vieja["publicada_en"]
    assert cuerpo["revisada"] is True
    assert cuerpo["publicacion"]["moderacion"] is None
    muro = client.get(PUBLICACIONES, headers=autor).json()["items"]
    assert [i["id"] for i in muro] == [nueva["id"], vieja["id"]]


@pytest.mark.parametrize(
    ("preparar", "accion"),
    [
        (None, "restaurar"),  # no está oculta
        ("revisar", "revisar"),  # ya revisada
        ("ocultar", "ocultar"),  # ya oculta
        ("ocultar", "revisar"),  # oculta: se restaura, no se revisa
    ],
)
def test_transiciones_no_permitidas(
    client: TestClient, usuario, publicar, moderar, preparar, accion
) -> None:
    pub = publicar(usuario())
    if preparar:
        extra = {"motivo": "Spam"} if preparar == "ocultar" else {}
        assert moderar(pub["id"], preparar, **extra).status_code == 200
    extra = {"motivo": "Spam"} if accion == "ocultar" else {}
    res = moderar(pub["id"], accion, **extra)
    assert res.status_code == 409
    assert res.json()["code"] == "moderation_not_allowed"


def test_publicacion_inexistente(moderar) -> None:
    res = moderar(999, "revisar", marca="1.0.-")
    assert res.status_code == 404
    assert res.json()["code"] == "post_not_found"
