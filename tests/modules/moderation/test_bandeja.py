"""F4.1, F4.2 · HU-19."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION, bandeja
from tests.modules.posts.conftest import PUBLICACIONES, categoria_id, formulario


def test_sin_revisar_primero_y_de_cualquier_audiencia(
    client: TestClient, usuario, publicar, admin
) -> None:
    autor = usuario()
    primera = publicar(autor, titulo="Primera", audiencia_id=4)  # Solo estudiantes
    segunda = publicar(autor, titulo="Segunda", audiencia_id=3)  # Solo profesores
    items = bandeja(client, admin)
    assert [i["id"] for i in items] == [segunda["id"], primera["id"]]
    assert all(not i["revisada"] for i in items)
    assert items[0]["marca"]


def test_revisada_sale_de_sin_revisar_y_sigue_en_el_muro(
    client: TestClient, usuario, publicar, admin, moderar
) -> None:
    autor = usuario()
    pub = publicar(autor)
    res = moderar(pub["id"], "revisar")
    assert res.status_code == 200, res.text
    assert res.json()["revisada"] is True
    assert bandeja(client, admin) == []
    assert [i["id"] for i in bandeja(client, admin, estado="revisada")] == [pub["id"]]
    muro = client.get(PUBLICACIONES, headers=autor).json()["items"]
    assert [i["id"] for i in muro] == [pub["id"]]


def test_editar_una_revisada_la_devuelve_a_sin_revisar(
    client: TestClient, usuario, publicar, admin, moderar
) -> None:
    autor = usuario()
    pub = publicar(autor)
    moderar(pub["id"], "revisar")
    res = client.put(
        f"{PUBLICACIONES}/{pub['id']}", headers=autor, data=formulario(titulo="Corregida")
    )
    assert res.status_code == 200, res.text
    assert [i["id"] for i in bandeja(client, admin)] == [pub["id"]]


def test_filtros_y_paginacion(client: TestClient, usuario, publicar, admin, moderar) -> None:
    ana = usuario()
    luis = usuario(correo="luis@facyt.uc.edu.ve")
    otra = sql("SELECT id FROM categoria WHERE nombre <> 'Avisos' LIMIT 1")[0][0]
    a = publicar(ana)
    b = publicar(luis, categoria_id=otra)
    c = publicar(ana)
    oculta = publicar(luis)
    moderar(oculta["id"], "ocultar", motivo="Spam")

    autor_ana = sql("SELECT id FROM usuario WHERE correo = 'estudiante@facyt.uc.edu.ve'")[0][0]
    assert [i["id"] for i in bandeja(client, admin, autor_id=autor_ana)] == [c["id"], a["id"]]
    assert [i["id"] for i in bandeja(client, admin, categoria_id=otra)] == [b["id"]]
    assert [i["id"] for i in bandeja(client, admin, categoria_id=categoria_id())] == [
        c["id"],
        a["id"],
    ]
    ocultas = bandeja(client, admin, estado="oculta")
    assert [i["id"] for i in ocultas] == [oculta["id"]]
    assert ocultas[0]["ocultada_en"] is not None
    assert len(bandeja(client, admin, estado="todas")) == 4

    pagina = client.get(f"{MODERACION}/publicaciones", headers=admin, params={"limite": 2}).json()
    assert [i["id"] for i in pagina["items"]] == [c["id"], b["id"]]
    resto = client.get(
        f"{MODERACION}/publicaciones",
        headers=admin,
        params={"limite": 2, "cursor": pagina["siguiente"]},
    ).json()
    assert [i["id"] for i in resto["items"]] == [a["id"]]
    assert resto["siguiente"] is None


def test_rango_de_fechas_invalido(client: TestClient, admin) -> None:
    res = client.get(
        f"{MODERACION}/publicaciones",
        headers=admin,
        params={"desde": "2026-09-10", "hasta": "2026-09-01"},
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "hasta"


def test_contador_de_pendientes(client: TestClient, usuario, publicar, admin, moderar) -> None:
    autor = usuario()
    pubs = [publicar(autor) for _ in range(3)]
    moderar(pubs[0]["id"], "revisar")
    moderar(pubs[1]["id"], "ocultar", motivo="Spam")
    res = client.get(f"{MODERACION}/publicaciones/pendientes", headers=admin)
    assert res.json() == {"total": 1, "ocultas": 1}


def test_editada_tras_ocultar(client: TestClient, usuario, publicar, admin, moderar) -> None:
    autor = usuario()
    pub = publicar(autor)
    moderar(pub["id"], "ocultar", motivo="Falta la fecha")
    assert bandeja(client, admin, estado="oculta")[0]["editada_tras_ocultar"] is False
    client.put(f"{PUBLICACIONES}/{pub['id']}", headers=autor, data=formulario(titulo="Con fecha"))
    fila = bandeja(client, admin, estado="oculta")[0]
    assert fila["editada_tras_ocultar"] is True
    assert fila["estado"]["codigo"] == "oculta_moderacion"  # sigue oculta (F2.7)
