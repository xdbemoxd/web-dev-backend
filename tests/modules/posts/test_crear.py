"""F2.1-F2.6 · HU-07, HU-08, HU-09, HU-10."""

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import get_settings
from tests.helpers import imagen, sql
from tests.modules.posts.conftest import PUBLICACIONES, archivos, categoria_id, formulario

MEDIA = Path(get_settings().storage_local_dir)


def _campos(res) -> list[str]:
    return [e["field"] for e in res.json().get("errors", [])]


def test_crear_nace_visible_y_sin_revisar(client: TestClient, usuario) -> None:
    headers = usuario()
    res = client.post(PUBLICACIONES, headers=headers, data=formulario(titulo="  Charla de IA  "))
    assert res.status_code == 201, res.text
    pub = res.json()
    assert res.headers["location"] == f"{PUBLICACIONES}/{pub['id']}"
    assert pub["titulo"] == "Charla de IA"
    assert pub["estado"]["codigo"] == "visible"
    assert pub["audiencia"]["codigo"] == "comunidad"  # por defecto (F2.3)
    assert pub["categoria"]["nombre"] == "Avisos"
    assert pub["autor"]["nombre_completo"] == "Ana Pérez"
    assert "correo" not in pub["autor"]
    assert pub["es_autor"] is True
    assert pub["editada_en"] is None
    assert sql("SELECT revisada FROM publicacion")[0][0] is False


def test_autor_y_fecha_no_se_pueden_enviar(client: TestClient, usuario) -> None:
    headers = usuario()
    datos = formulario(autor_id=999, publicada_en="2020-01-01T00:00:00Z", estado_id=3)
    pub = client.post(PUBLICACIONES, headers=headers, data=datos).json()
    fila = sql(
        "SELECT autor_id, estado_id, publicada_en > now() - interval '1 minute' FROM publicacion"
    )
    assert fila[0][0] != 999
    assert fila[0][1] == 1
    assert fila[0][2] is True
    assert pub["estado"]["codigo"] == "visible"


def test_validaciones_de_titulo_y_contenido(client: TestClient, usuario) -> None:
    headers = usuario()
    for datos, campo in [
        (formulario(titulo="ab"), "titulo"),
        (formulario(titulo="x" * 151), "titulo"),
        (formulario(titulo="   "), "titulo"),
        (formulario(contenido="   "), "contenido"),
        (formulario(contenido="x" * 10_001), "contenido"),
    ]:
        res = client.post(PUBLICACIONES, headers=headers, data=datos)
        assert res.status_code == 422, datos
        assert campo in _campos(res)
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 0


def test_categoria_inactiva_o_inexistente(client: TestClient, usuario) -> None:
    headers = usuario()
    sql("UPDATE categoria SET activa = false WHERE nombre = 'Logros'")
    for cid in (categoria_id("Logros"), 9999):
        res = client.post(PUBLICACIONES, headers=headers, data=formulario(categoria_id=cid))
        assert res.status_code == 422
        assert _campos(res) == ["categoria_id"]


def test_audiencia_invalida(client: TestClient, usuario) -> None:
    res = client.post(PUBLICACIONES, headers=usuario(), data=formulario(audiencia_id=9))
    assert res.status_code == 422
    assert "audiencia_id" in _campos(res)


def test_cualquier_autor_elige_cualquier_audiencia(client: TestClient, usuario, publicar) -> None:
    pub = publicar(usuario(), audiencia_id=3)  # un estudiante publica para profesores
    assert pub["audiencia"]["codigo"] == "solo_profesores"


def test_etiquetas_se_normalizan_y_no_se_repiten(client: TestClient, usuario, publicar) -> None:
    pub = publicar(usuario(), etiquetas=["Jornadas 2026", "jornadas2026", "Diseño Web", "IA"])
    assert pub["etiquetas"] == ["jornadas2026", "diseñoweb", "ia"]
    assert sql("SELECT count(*) FROM etiqueta")[0][0] == 3


def test_maximo_cinco_etiquetas_tras_normalizar(client: TestClient, usuario) -> None:
    headers = usuario()
    seis = ["a", "b", "c", "d", "e", "f"]
    res = client.post(PUBLICACIONES, headers=headers, data=formulario(etiquetas=seis))
    assert res.status_code == 422
    assert _campos(res) == ["etiquetas"]
    # Seis escritas, cinco distintas: se acepta.
    res = client.post(PUBLICACIONES, headers=headers, data=formulario(etiquetas=[*seis[:5], "A"]))
    assert res.status_code == 201


def test_etiqueta_sin_letras_se_rechaza(client: TestClient, usuario) -> None:
    res = client.post(PUBLICACIONES, headers=usuario(), data=formulario(etiquetas=["ok", "!!!"]))
    assert res.status_code == 422
    assert _campos(res) == ["etiquetas.1"]


def test_imagenes_se_guardan_como_webp_en_orden(client: TestClient, usuario) -> None:
    grande = imagen("JPEG", (3000, 1500), exif=b"Exif\x00\x00secreto")
    res = client.post(
        PUBLICACIONES,
        headers=usuario(),
        data=formulario(),
        files=archivos(imagen(), grande),
    )
    assert res.status_code == 201, res.text
    imagenes = res.json()["imagenes"]
    assert [i["orden"] for i in imagenes] == [1, 2]
    filas = sql("SELECT ruta_almacenamiento, tipo_mime FROM imagen_publicacion ORDER BY orden")
    assert all(tipo == "image/webp" for _, tipo in filas)
    assert all("foto" not in ruta for ruta, _ in filas)  # el nombre original no se conserva
    with Image.open(MEDIA / filas[1][0]) as img:
        assert img.format == "WEBP"
        assert img.size == (2048, 1024)  # lado mayor reducido, sin recortar
        assert "exif" not in img.info


def test_maximo_cinco_imagenes(client: TestClient, usuario) -> None:
    res = client.post(
        PUBLICACIONES,
        headers=usuario(),
        data=formulario(),
        files=archivos(*[imagen() for _ in range(6)]),
    )
    assert res.status_code == 422
    assert _campos(res) == ["imagenes"]


def test_imagen_invalida_no_deja_nada(client: TestClient, usuario) -> None:
    antes = set(MEDIA.rglob("*"))
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    for malo in (b"no soy una imagen", gif.getvalue(), b"\xff" * (5 * 1024 * 1024 + 1)):
        res = client.post(
            PUBLICACIONES,
            headers=usuario(correo=f"u{len(malo)}@facyt.uc.edu.ve"),
            data=formulario(),
            files=archivos(imagen(), imagen(), malo),
        )
        assert res.status_code == 422
        assert _campos(res) == ["imagenes.2"]
    assert sql("SELECT count(*) FROM publicacion")[0][0] == 0
    assert set(MEDIA.rglob("*")) == antes


def test_sin_sesion_no_se_publica(client: TestClient) -> None:
    res = client.post(PUBLICACIONES, data=formulario())
    assert res.status_code == 401


def test_categorias_activas(client: TestClient) -> None:
    sql("UPDATE categoria SET activa = false WHERE nombre = 'Logros'")
    nombres = [c["nombre"] for c in client.get("/api/v1/categorias").json()]
    assert "Logros" not in nombres
    assert "Avisos" in nombres
    assert nombres == sorted(nombres)
