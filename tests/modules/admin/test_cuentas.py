"""F5.4-F5.6 · HU-24."""

from fastapi.testclient import TestClient

from tests.helpers import PASSWORD, sql
from tests.modules.admin.conftest import ADMIN, get, id_de
from tests.modules.posts.conftest import PUBLICACIONES

ANA = "estudiante@facyt.uc.edu.ve"
ADMIN_CORREO = "administrador@facyt.uc.edu.ve"


def _rol(client: TestClient, headers: dict, uid: int, rol_id: int, **extra):
    return client.put(
        f"{ADMIN}/usuarios/{uid}/rol", headers=headers, json={"rol_id": rol_id, **extra}
    )


def _estado(client: TestClient, headers: dict, uid: int, accion: str, **cuerpo):
    return client.post(f"{ADMIN}/usuarios/{uid}/{accion}", headers=headers, json=cuerpo)


def test_cambio_de_rol_queda_registrado(client: TestClient, usuario, admin) -> None:
    usuario()
    res = _rol(client, admin, id_de(ANA), 3, motivo="Apoyo en moderación")
    assert res.status_code == 200, res.text
    cuerpo = res.json()
    assert cuerpo["rol"]["codigo"] == "administrador"
    (evento,) = cuerpo["eventos"]
    assert evento["tipo"] == "cambio_rol"
    assert evento["realizado_por"]["id"] == id_de(ADMIN_CORREO)
    assert (evento["rol_anterior"]["codigo"], evento["rol_nuevo"]["codigo"]) == (
        "estudiante",
        "administrador",
    )
    assert evento["motivo"] == "Apoyo en moderación"
    assert evento["fecha"]
    assert _rol(client, admin, id_de(ANA), 3).json()["code"] == "no_changes"


def test_no_puede_quitarse_su_propio_rol(client: TestClient, usuario, admin) -> None:
    usuario("administrador", correo="otra@facyt.uc.edu.ve")  # hay otro admin: igual no puede
    res = _rol(client, admin, id_de(ADMIN_CORREO), 1)
    assert res.status_code == 409
    assert res.json()["code"] == "self_demotion"
    assert (
        _estado(client, admin, id_de(ADMIN_CORREO), "desactivar", motivo="x").json()["code"]
        == "self_deactivation"
    )
    assert sql("SELECT count(*) FROM evento_cuenta")[0][0] == 0


def test_desactivar_cierra_sesiones_y_conserva_publicaciones(
    client: TestClient, usuario, publicar, admin
) -> None:
    ana = usuario()
    pub = publicar(ana, audiencia_id=1)
    uid = id_de(ANA)
    assert _estado(client, admin, uid, "desactivar", motivo="  ").status_code == 422

    res = _estado(client, admin, uid, "desactivar", motivo="Spam reiterado")
    assert res.status_code == 200, res.text
    assert res.json()["activo"] is False
    assert res.json()["eventos"][0]["tipo"] == "desactivacion"
    assert client.get("/api/v1/usuarios/me", headers=ana).status_code == 401
    login = client.post("/api/v1/auth/login", json={"correo": ANA, "password": PASSWORD})
    assert login.status_code == 401
    assert login.json()["code"] == "invalid_credentials"  # aviso genérico (F1.4)
    assert (
        sql("SELECT count(*) FROM sesion WHERE usuario_id = :u AND revocada_en IS NULL", u=uid)[0][
            0
        ]
        == 0
    )
    assert [p["id"] for p in client.get(PUBLICACIONES).json()["items"]] == [pub["id"]]
    assert _estado(client, admin, uid, "desactivar", motivo="x").json()["code"] == "no_changes"


def test_reactivar_conserva_el_rol(client: TestClient, usuario, admin) -> None:
    usuario("profesor")
    uid = id_de("profesor@facyt.uc.edu.ve")
    _estado(client, admin, uid, "desactivar", motivo="Error de carga")
    res = _estado(client, admin, uid, "reactivar")
    assert res.status_code == 200
    assert res.json()["activo"] is True
    assert res.json()["rol"]["codigo"] == "profesor"
    login = client.post(
        "/api/v1/auth/login", json={"correo": "profesor@facyt.uc.edu.ve", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert [e["tipo"] for e in res.json()["eventos"]] == ["reactivacion", "desactivacion"]
    assert _estado(client, admin, uid, "reactivar").json()["code"] == "no_changes"


def test_lista_busqueda_y_filtros(client: TestClient, usuario, admin, registrar) -> None:
    registrar("jose@facyt.uc.edu.ve", nombre_completo="José Martínez")
    usuario("profesor")
    usuario()
    assert [u["correo"] for u in get(client, admin, "/usuarios", q="jose")["items"]] == [
        "jose@facyt.uc.edu.ve"
    ]  # sin tildes
    assert get(client, admin, "/usuarios", q="MARTINEZ")["total"] == 1
    assert get(client, admin, "/usuarios", q="profesor@")["items"][0]["rol"]["codigo"] == (
        "profesor"
    )
    assert get(client, admin, "/usuarios", rol_id=3)["total"] == 1
    sql("UPDATE usuario SET activo = false WHERE correo = 'jose@facyt.uc.edu.ve'")
    inactivas = get(client, admin, "/usuarios", activo="false")
    assert [u["correo"] for u in inactivas["items"]] == ["jose@facyt.uc.edu.ve"]
    pagina = get(client, admin, "/usuarios", size=2, page=2)
    assert (pagina["total"], pagina["pages"], len(pagina["items"])) == (4, 2, 2)
    res = client.get(f"{ADMIN}/usuarios", headers=admin, params={"q": "a"})
    assert res.status_code == 422
    assert client.get(f"{ADMIN}/usuarios/999", headers=admin).status_code == 404
