"""F5.1-F5.3 · HU-23."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.admin.conftest import ADMIN, get, id_de, pedir_profesor, solicitud_pendiente
from tests.modules.posts.conftest import PUBLICACIONES

ANA = "estudiante@facyt.uc.edu.ve"


def _resolver(client: TestClient, admin: dict, sid: int, accion: str, **cuerpo):
    return client.post(
        f"{ADMIN}/solicitudes-profesor/{sid}/{accion}", headers=admin, json=cuerpo or None
    )


def test_bandeja_mas_antigua_primero_y_contador(client: TestClient, usuario, admin) -> None:
    primera = usuario(correo="primera@facyt.uc.edu.ve")
    segunda = usuario(correo="segunda@facyt.uc.edu.ve")
    pedir_profesor(client, primera)
    pedir_profesor(client, segunda)

    assert get(client, admin, "/pendientes") == {"solicitudes": 2}
    pagina = get(client, admin, "/solicitudes-profesor")
    assert [s["solicitante"]["correo"] for s in pagina["items"]] == [
        "primera@facyt.uc.edu.ve",
        "segunda@facyt.uc.edu.ve",
    ]
    assert pagina["total"] == 2
    assert pagina["items"][0]["estado"] == "pendiente"
    assert pagina["items"][0]["aplica"] is True


def test_aprobar_da_rol_de_profesor_desde_la_siguiente_accion(
    client: TestClient, usuario, publicar, admin
) -> None:
    ana = usuario()
    profe = publicar(admin, titulo="Consejo de facultad", audiencia_id=3)  # Solo profesores
    alumnos = publicar(admin, titulo="Becas", audiencia_id=4)  # Solo estudiantes
    pedir_profesor(client, ana)
    ids = lambda: {p["id"] for p in client.get(PUBLICACIONES, headers=ana).json()["items"]}  # noqa: E731
    assert ids() == {alumnos["id"]}

    res = _resolver(client, admin, solicitud_pendiente(ANA), "aprobar")
    assert res.status_code == 200, res.text
    assert res.json()["estado"] == "aprobada"
    assert res.json()["resuelta_por"]["id"] == id_de("administrador@facyt.uc.edu.ve")
    assert ids() == {profe["id"]}  # mismo token, rol nuevo (F1.5)

    perfil = client.get("/api/v1/usuarios/me", headers=ana).json()
    assert perfil["rol"]["codigo"] == "profesor"
    assert perfil["solicitud_profesor"]["estado"] == "aprobada"
    evento = sql(
        "SELECT tipo_evento_id, rol_anterior_id, rol_nuevo_id, solicitud_id IS NOT NULL "
        "FROM evento_cuenta"
    )
    assert evento == [(1, 1, 2, True)]


def test_rechazar_exige_motivo(client: TestClient, usuario, admin) -> None:
    pedir_profesor(client, usuario())
    sid = solicitud_pendiente(ANA)
    for cuerpo in ({}, {"motivo": "   "}):
        res = _resolver(client, admin, sid, "rechazar", **cuerpo)
        assert res.status_code == 422
    assert sql("SELECT estado_id FROM solicitud_profesor")[0][0] == 1


def test_no_se_resuelve_dos_veces(client: TestClient, usuario, admin) -> None:
    pedir_profesor(client, usuario())
    sid = solicitud_pendiente(ANA)
    assert _resolver(client, admin, sid, "rechazar", motivo="Sin carnet").status_code == 200
    for accion, cuerpo in (("aprobar", {}), ("rechazar", {"motivo": "Otra vez"})):
        res = _resolver(client, admin, sid, accion, **cuerpo)
        assert res.status_code == 409
        assert res.json()["code"] == "request_already_resolved"
    assert sql("SELECT rol_id FROM usuario WHERE correo = :c", c=ANA)[0][0] == 1
    assert _resolver(client, admin, 999, "aprobar").status_code == 404


def test_rechazo_visible_y_nueva_solicitud_con_historial(
    client: TestClient, usuario, admin
) -> None:
    ana = usuario()
    pedir_profesor(client, ana)
    _resolver(client, admin, solicitud_pendiente(ANA), "rechazar", motivo="Falta constancia")
    perfil = client.get("/api/v1/usuarios/me", headers=ana).json()
    assert perfil["rol"]["codigo"] == "estudiante"
    assert perfil["solicitud_profesor"]["motivo_rechazo"] == "Falta constancia"

    pedir_profesor(client, ana)  # tras un rechazo puede volver a pedirlo (F5.3)
    _resolver(client, admin, solicitud_pendiente(ANA), "rechazar", motivo="Constancia vencida")
    pedir_profesor(client, ana)

    (fila,) = get(client, admin, "/solicitudes-profesor")["items"]
    assert fila["rechazos_previos"] == 2
    detalle = get(client, admin, f"/solicitudes-profesor/{fila['id']}")
    assert [(h["estado"], h["motivo_rechazo"]) for h in detalle["historial"]] == [
        ("pendiente", None),
        ("rechazada", "Constancia vencida"),
        ("rechazada", "Falta constancia"),
    ]
    rechazadas = get(client, admin, "/solicitudes-profesor", estado="rechazada")
    assert rechazadas["total"] == 2


def test_no_aplica_si_ya_no_es_estudiante(client: TestClient, usuario, admin) -> None:
    pedir_profesor(client, usuario())
    uid = id_de(ANA)
    res = client.put(f"{ADMIN}/usuarios/{uid}/rol", headers=admin, json={"rol_id": 2})
    assert res.status_code == 200
    sid = solicitud_pendiente(ANA)
    assert get(client, admin, f"/solicitudes-profesor/{sid}")["aplica"] is False
    res = _resolver(client, admin, sid, "aprobar")
    assert res.status_code == 409
    assert res.json()["code"] == "request_not_applicable"
    assert sql("SELECT estado_id FROM solicitud_profesor")[0][0] == 1  # rollback completo
    assert _resolver(client, admin, sid, "rechazar", motivo="Ya es profesor").status_code == 200
