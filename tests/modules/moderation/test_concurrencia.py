"""Dos administradores, o el autor, cambian la publicación mientras otro la revisa."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION, marca
from tests.modules.posts.conftest import PUBLICACIONES, formulario


def test_otro_admin_actuo_primero(client: TestClient, usuario, publicar, moderar, admin) -> None:
    pub = publicar(usuario())
    otro = usuario("administrador", correo="otro@facyt.uc.edu.ve")
    vista = marca(client, admin, pub["id"])  # los dos cargan la misma versión
    assert moderar(pub["id"], "ocultar", headers=otro, motivo="Spam").status_code == 200

    res = moderar(pub["id"], "revisar", marca=vista)
    assert res.status_code == 409
    assert res.json()["code"] == "moderation_conflict"
    assert "Ana Pérez la ocultó" in res.json()["detail"]
    assert sql("SELECT count(*) FROM moderacion")[0][0] == 1  # no se registró nada más


def test_el_autor_edito_mientras_se_revisaba(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    autor = usuario()
    pub = publicar(autor)
    vista = marca(client, admin, pub["id"])
    client.put(f"{PUBLICACIONES}/{pub['id']}", headers=autor, data=formulario(titulo="Cambiada"))

    res = moderar(pub["id"], "revisar", marca=vista)
    assert res.status_code == 409
    assert res.json()["code"] == "moderation_conflict"
    assert sql("SELECT revisada FROM publicacion")[0][0] is False


def test_la_respuesta_trae_la_marca_nueva(client: TestClient, usuario, publicar, admin) -> None:
    pub = publicar(usuario())
    vista = marca(client, admin, pub["id"])
    res = client.post(
        f"{MODERACION}/publicaciones/{pub['id']}/ocultar",
        headers=admin,
        json={"marca": vista, "motivo": "Spam"},
    )
    nueva = res.json()["marca"]
    assert nueva != vista
    res = client.post(
        f"{MODERACION}/publicaciones/{pub['id']}/restaurar", headers=admin, json={"marca": nueva}
    )
    assert res.status_code == 200
