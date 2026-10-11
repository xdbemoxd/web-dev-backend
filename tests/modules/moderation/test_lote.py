"""F4.2 en lote."""

from fastapi.testclient import TestClient

from tests.helpers import sql
from tests.modules.moderation.conftest import MODERACION, bandeja, marca


def test_revisa_las_vigentes_y_omite_las_que_cambiaron(
    client: TestClient, usuario, publicar, moderar, admin
) -> None:
    autor = usuario()
    a, b, c = publicar(autor), publicar(autor), publicar(autor)
    items = [{"id": f["id"], "marca": f["marca"]} for f in bandeja(client, admin)]
    moderar(b["id"], "ocultar", motivo="Spam")  # cambia después de cargar la bandeja
    items.append({"id": 999, "marca": "1.0.-"})

    res = client.post(
        f"{MODERACION}/publicaciones/revisar-lote", headers=admin, json={"items": items}
    )
    assert res.status_code == 200, res.text
    cuerpo = res.json()
    assert sorted(cuerpo["revisadas"]) == sorted([a["id"], c["id"]])
    assert {(o["id"], o["code"]) for o in cuerpo["omitidas"]} == {
        (b["id"], "moderation_conflict"),
        (999, "post_not_found"),
    }
    assert bandeja(client, admin) == []
    assert sql("SELECT count(*) FROM moderacion WHERE accion_id = 1")[0][0] == 2
    assert marca(client, admin, a["id"]).split(".")[1] == "1"


def test_limites_del_lote(client: TestClient, admin) -> None:
    url = f"{MODERACION}/publicaciones/revisar-lote"
    assert client.post(url, headers=admin, json={"items": []}).status_code == 422
    muchos = [{"id": i, "marca": "x"} for i in range(1, 52)]
    assert client.post(url, headers=admin, json={"items": muchos}).status_code == 422
