"""F4.6 · HU-22."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION


def _historial(client: TestClient, headers: dict, **params) -> list[dict]:
    res = client.get(f"{MODERACION}/historial", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return res.json()["items"]


def test_cada_accion_queda_registrada_en_el_detalle(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    pub = publicar(usuario())
    otro = usuario("administrador", correo="otro@facyt.uc.edu.ve")
    moderar(pub["id"], "ocultar", headers=otro, motivo="Información incorrecta")
    moderar(pub["id"], "restaurar")

    detalle = client.get(f"{MODERACION}/publicaciones/{pub['id']}", headers=admin).json()
    acciones = [(r["accion"]["codigo"], r["motivo"]) for r in detalle["historial"]]
    assert acciones == [("restaurar", None), ("ocultar", "Información incorrecta")]
    ocultar = detalle["historial"][1]
    assert (
        ocultar["moderador"]["id"]
        == sql("SELECT id FROM usuario WHERE correo = 'otro@facyt.uc.edu.ve'")[0][0]
    )
    assert ocultar["fecha"]
    assert ocultar["titulo"] == "Defensa de TEG"


def test_eliminada_conserva_titulo_autor_y_motivo(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    autor = usuario()
    pub = publicar(autor, titulo="Venta de apuntes")
    moderar(pub["id"], "eliminar", motivo="Comercial")
    (registro,) = _historial(client, admin)
    assert registro["publicacion_id"] is None
    assert registro["titulo"] == "Venta de apuntes"
    assert registro["autor"]["nombre_completo"] == "Ana Pérez"
    assert registro["motivo"] == "Comercial"
    assert registro["accion"]["codigo"] == "eliminar"


def test_filtros_por_administrador_accion_y_fecha(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    autor = usuario()
    otro = usuario("administrador", correo="otro@facyt.uc.edu.ve")
    a, b = publicar(autor), publicar(autor)
    moderar(a["id"], "revisar")
    moderar(b["id"], "ocultar", headers=otro, motivo="Spam")

    otro_id = sql("SELECT id FROM usuario WHERE correo = 'otro@facyt.uc.edu.ve'")[0][0]
    assert [r["accion"]["codigo"] for r in _historial(client, admin, moderador_id=otro_id)] == [
        "ocultar"
    ]
    assert [r["publicacion_id"] for r in _historial(client, admin, accion=1)] == [a["id"]]
    assert _historial(client, admin, hasta="2000-01-01") == []
    assert len(_historial(client, admin)) == 2

    moderadores = client.get(f"{MODERACION}/moderadores", headers=admin).json()
    assert {m["id"] for m in moderadores} == {otro_id, _admin_id()}


def _admin_id() -> int:
    return sql("SELECT id FROM usuario WHERE correo = 'administrador@facyt.uc.edu.ve'")[0][0]


def test_historial_inmutable_en_la_base(usuario, publicar, moderar) -> None:
    pub = publicar(usuario())
    moderar(pub["id"], "revisar")
    try:
        sql("UPDATE moderacion SET motivo = 'x'")
    except Exception as exc:  # el trigger lo rechaza (F4.6)
        assert "no se puede modificar" in str(exc)
    else:
        raise AssertionError("el historial se pudo modificar")
