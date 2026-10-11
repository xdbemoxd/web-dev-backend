"""F5.7-F5.9 · HU-25."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.admin.conftest import ADMIN, get
from tests.modules.posts.conftest import PUBLICACIONES, formulario


def _crear(client: TestClient, admin: dict, **datos):
    return client.post(f"{ADMIN}/categorias", headers=admin, json=datos)


def _limpiar_nuevas() -> None:
    # clean_db no toca los catálogos: se borran las creadas en la prueba.
    sql("DELETE FROM categoria WHERE id > 10")


def test_crear_aparece_en_el_formulario(client: TestClient, admin) -> None:
    try:
        res = _crear(client, admin, nombre="  Pasantías ", descripcion="Ofertas", color="#0f766e")
        assert res.status_code == 201, res.text
        cat = res.json()
        assert (cat["nombre"], cat["color"], cat["activa"], cat["publicaciones"]) == (
            "Pasantías",
            "#0F766E",
            True,
            0,
        )
        assert res.headers["Location"].endswith(f"/admin/categorias/{cat['id']}")
        assert "Pasantías" in [c["nombre"] for c in client.get("/api/v1/categorias").json()]
    finally:
        _limpiar_nuevas()


def test_nombre_repetido_sin_distinguir_mayusculas(client: TestClient, admin) -> None:
    res = _crear(client, admin, nombre="avisos")
    assert res.status_code == 409
    assert res.json()["code"] == "category_name_taken"
    assert res.json()["errors"][0]["field"] == "nombre"
    otra = next(c for c in get(client, admin, "/categorias") if c["nombre"] == "Logros")
    res = client.put(f"{ADMIN}/categorias/{otra['id']}", headers=admin, json={"nombre": "AVISOS"})
    assert res.status_code == 409
    assert sql("SELECT nombre FROM categoria WHERE id = :i", i=otra["id"])[0][0] == "Logros"


def test_validaciones(client: TestClient, admin) -> None:
    assert _crear(client, admin, nombre="X").status_code == 422
    assert _crear(client, admin, nombre="Rojo", color="red").status_code == 422
    assert (
        client.put(f"{ADMIN}/categorias/999", headers=admin, json={"nombre": "Nada"}).status_code
        == 404
    )


def test_renombrar_se_ve_en_sus_publicaciones(client: TestClient, usuario, publicar, admin) -> None:
    autor = usuario()
    pub = publicar(autor)  # categoría "Avisos"
    cat_id = pub["categoria"]["id"]
    try:
        res = client.put(
            f"{ADMIN}/categorias/{cat_id}",
            headers=admin,
            json={"nombre": "Anuncios", "descripcion": "Avisos oficiales"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["publicaciones"] == 1
        assert res.json()["color"] == pub["categoria"]["color"]  # renombrar no borra el color
        detalle = client.get(f"{PUBLICACIONES}/{pub['id']}", headers=autor).json()
        assert detalle["categoria"]["nombre"] == "Anuncios"
    finally:
        sql("UPDATE categoria SET nombre = 'Avisos' WHERE id = :i", i=cat_id)


def test_desactivar_la_quita_del_formulario_pero_no_del_filtro(
    client: TestClient, usuario, publicar, admin
) -> None:
    autor = usuario()
    pub = publicar(autor)
    cat_id = pub["categoria"]["id"]
    res = client.put(f"{ADMIN}/categorias/{cat_id}/estado", headers=admin, json={"activa": False})
    assert res.status_code == 200
    assert res.json()["activa"] is False

    activas = [c["id"] for c in client.get("/api/v1/categorias").json()]
    assert cat_id not in activas
    todas = client.get("/api/v1/categorias", params={"incluir_inactivas": True}).json()
    assert cat_id in [c["id"] for c in todas]
    res = client.post(PUBLICACIONES, headers=autor, data=formulario(categoria_id=cat_id))
    assert res.status_code == 422
    muro = client.get(PUBLICACIONES, headers=autor, params={"categoria_id": cat_id}).json()
    assert [p["id"] for p in muro["items"]] == [pub["id"]]

    res = client.put(f"{ADMIN}/categorias/{cat_id}/estado", headers=admin, json={"activa": True})
    assert res.json()["activa"] is True
    assert sql("SELECT count(*) FROM categoria")[0][0] == 10  # nunca se borra
