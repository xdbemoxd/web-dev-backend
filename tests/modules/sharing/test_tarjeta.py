"""F6.1-F6.3 · HU-27."""

import io

from fastapi.testclient import TestClient
from PIL import Image

from tests.helpers import sql
from tests.modules.posts.conftest import PUBLICACIONES, formulario
from tests.modules.posts.test_detalle import ocultar_por_moderacion
from tests.modules.sharing.conftest import COMUNIDAD, PUBLICA, SOLO_PROFESORES, abrir, tarjeta


def _foto_roja() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1000), (220, 20, 20)).save(buf, "PNG")
    return buf.getvalue()


def test_publica_con_imagen_usa_la_portada(client: TestClient, usuario) -> None:
    autor = usuario()
    res = client.post(
        PUBLICACIONES,
        headers=autor,
        data=formulario(audiencia_id=PUBLICA),
        files=[("imagenes", ("roja.png", _foto_roja(), "image/png"))],
    )
    pub = res.json()
    res = tarjeta(client, autor, pub["id"])
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/jpeg"
    assert res.headers["content-disposition"].startswith("inline")
    img = abrir(res.content)
    assert (img.format, img.size, img.mode) == ("JPEG", (1080, 1080), "RGB")
    assert "exif" not in img.info
    r, g, b = img.getpixel((540, 300))  # zona sin texto ni velo: se ve la foto roja
    assert r > 150 and g < 90 and b < 90


def test_publica_sin_imagen_usa_la_plantilla_de_su_categoria(
    client: TestClient, usuario, publicar
) -> None:
    autor = usuario()
    pub = publicar(autor, audiencia_id=PUBLICA)  # categoría Avisos, #A16207
    img = abrir(tarjeta(client, autor, pub["id"]).content)
    r, g, b = img.getpixel((1000, 400))
    assert r > g > b and r > 110  # ámbar oscurecido para el contraste, no la foto ni gris


def test_solo_publicas(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    for audiencia in (SOLO_PROFESORES, COMUNIDAD):
        pub = publicar(autor, audiencia_id=audiencia)
        res = tarjeta(client, autor, pub["id"])
        assert res.status_code == 409
        assert res.json()["code"] == "card_requires_public"
        assert "Pública" in res.json()["detail"]  # sugiere cambiar la audiencia (F6.1)


def test_oculta_no_genera_tarjeta(client: TestClient, usuario, publicar, admin) -> None:
    autor = usuario()
    pub = publicar(autor, audiencia_id=PUBLICA)
    client.put(f"{PUBLICACIONES}/{pub['id']}/visibilidad", headers=autor, json={"visible": False})
    assert tarjeta(client, autor, pub["id"]).json()["code"] == "card_post_hidden"
    otra = publicar(autor, audiencia_id=PUBLICA)
    ocultar_por_moderacion(otra["id"])
    assert tarjeta(client, admin, otra["id"]).json()["code"] == "card_post_hidden"


def test_solo_autor_o_administrador(client: TestClient, usuario, publicar, admin) -> None:
    pub = publicar(usuario(), audiencia_id=PUBLICA)
    lector = usuario("profesor")
    res = tarjeta(client, lector, pub["id"])
    assert res.status_code == 403
    assert res.json()["code"] == "card_forbidden"
    assert tarjeta(client, {}, pub["id"]).status_code == 401
    assert tarjeta(client, admin, pub["id"]).status_code == 200  # admin aunque no sea el autor
    assert tarjeta(client, admin, 999).status_code == 404


def test_descarga_con_el_titulo_en_el_nombre(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, audiencia_id=PUBLICA, titulo="Defensa de TEG: Química ¡hoy!")
    res = tarjeta(client, autor, pub["id"], descargar=True)
    disposicion = res.headers["content-disposition"]
    assert disposicion.startswith("attachment")
    assert 'filename="red-facyt-defensa-de-teg-quimica-hoy.jpg"' in disposicion


def test_etag_304_y_cambia_al_editar(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, audiencia_id=PUBLICA)
    etag = tarjeta(client, autor, pub["id"]).headers["etag"]
    assert "private" in tarjeta(client, autor, pub["id"]).headers["cache-control"]
    res = tarjeta(client, {**autor, "If-None-Match": etag}, pub["id"])
    assert res.status_code == 304
    assert res.content == b""

    client.put(
        f"{PUBLICACIONES}/{pub['id']}",
        headers=autor,
        data=formulario(titulo="Nuevo título", audiencia_id=PUBLICA),
    )
    res = tarjeta(client, {**autor, "If-None-Match": etag}, pub["id"])
    assert res.status_code == 200
    assert res.headers["etag"] != etag

    # Un 304 no esquiva las reglas: si dejó de ser pública, 409 aunque el ETag sea viejo.
    sql("UPDATE publicacion SET audiencia_id = 2 WHERE id = :i", i=pub["id"])
    assert tarjeta(client, {**autor, "If-None-Match": etag}, pub["id"]).status_code == 409


def test_eliminada_da_404(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, audiencia_id=PUBLICA)
    client.delete(f"{PUBLICACIONES}/{pub['id']}", headers=autor)
    assert tarjeta(client, autor, pub["id"]).status_code == 404
