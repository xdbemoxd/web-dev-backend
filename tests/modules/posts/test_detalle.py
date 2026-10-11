"""F3.2 y F3.6 (mínimo del M3): quién ve una publicación según audiencia y estado."""

import pytest
from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.posts.conftest import PUBLICACIONES

# Tabla 1 del documento de requisitos: audiencia -> quién la ve.
MATRIZ = {
    1: {"visitante", "estudiante", "profesor", "administrador"},
    2: {"estudiante", "profesor", "administrador"},
    3: {"profesor", "administrador"},
    4: {"estudiante", "administrador"},
}


def ocultar_por_moderacion(pub_id: int, motivo: str = "Contenido duplicado") -> None:
    admin = sql("SELECT id FROM usuario WHERE rol_id = 3 LIMIT 1")[0][0]
    sql(
        "INSERT INTO moderacion (publicacion_id, accion_id, moderador_id, motivo, "
        "titulo_al_momento, autor_id_al_momento) "
        "SELECT id, 2, :admin, :motivo, titulo, autor_id FROM publicacion WHERE id = :id",
        admin=admin,
        motivo=motivo,
        id=pub_id,
    )
    sql("UPDATE publicacion SET estado_id = 3, revisada = true WHERE id = :id", id=pub_id)


@pytest.fixture
def lectores(usuario) -> dict[str, dict[str, str]]:
    return {
        "visitante": {},
        "estudiante": usuario("estudiante"),
        "profesor": usuario("profesor"),
        "administrador": usuario("administrador"),
    }


@pytest.mark.parametrize("audiencia", [1, 2, 3, 4])
def test_matriz_de_audiencia(
    client: TestClient, usuario, publicar, lectores, audiencia: int
) -> None:
    pub = publicar(usuario("profesor", "autor@facyt.uc.edu.ve"), audiencia_id=audiencia)
    for lector, headers in lectores.items():
        res = client.get(f"{PUBLICACIONES}/{pub['id']}", headers=headers)
        esperado = 200 if lector in MATRIZ[audiencia] else 403
        assert res.status_code == esperado, (audiencia, lector, res.text)


def test_autor_siempre_ve_lo_suyo(client: TestClient, usuario, publicar) -> None:
    autor = usuario("estudiante")
    pub = publicar(autor, audiencia_id=3)  # Solo profesores, pero es suya
    assert client.get(f"{PUBLICACIONES}/{pub['id']}", headers=autor).status_code == 200


def test_oculta_solo_la_ven_autor_y_admin(client: TestClient, usuario, publicar, lectores) -> None:
    autor = usuario("profesor", "autor@facyt.uc.edu.ve")
    pub = publicar(autor, audiencia_id=1)
    client.put(f"{PUBLICACIONES}/{pub['id']}/visibilidad", headers=autor, json={"visible": False})
    url = f"{PUBLICACIONES}/{pub['id']}"
    assert client.get(url, headers=autor).status_code == 200
    assert client.get(url, headers=lectores["administrador"]).status_code == 200
    for lector in ("visitante", "estudiante", "profesor"):
        assert client.get(url, headers=lectores[lector]).status_code == 403


def test_motivo_de_moderacion_solo_para_autor_y_admin(
    client: TestClient, usuario, publicar, lectores
) -> None:
    autor = usuario("profesor", "autor@facyt.uc.edu.ve")
    pub = publicar(autor)
    ocultar_por_moderacion(pub["id"])
    url = f"{PUBLICACIONES}/{pub['id']}"
    visto = client.get(url, headers=autor).json()
    assert visto["estado"]["codigo"] == "oculta_moderacion"
    assert visto["moderacion"]["motivo"] == "Contenido duplicado"
    assert client.get(url, headers=lectores["administrador"]).json()["moderacion"] is not None
    assert client.get(url, headers=lectores["estudiante"]).status_code == 403


def test_no_existe_frente_a_restringida(client: TestClient, usuario, publicar) -> None:
    pub = publicar(usuario(), audiencia_id=2)
    assert client.get(f"{PUBLICACIONES}/999999").status_code == 404
    res = client.get(f"{PUBLICACIONES}/{pub['id']}")
    assert res.status_code == 403
    assert res.json()["code"] == "post_restricted"
