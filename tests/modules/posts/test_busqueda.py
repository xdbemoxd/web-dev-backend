"""F3.4 · HU-15: búsqueda por texto en el histórico, con resaltado seguro."""

from fastapi.testclient import TestClient

from tests.modules.posts.conftest import PUBLICACIONES, categoria_id


def buscar(client: TestClient, headers: dict, texto: str, **params) -> dict:
    res = client.get(PUBLICACIONES, headers=headers, params={"texto": texto, **params})
    assert res.status_code == 200, res.text
    return res.json()


def _ids(datos: dict) -> list[int]:
    return [p["id"] for p in datos["items"]]


def test_sin_tildes_ni_mayusculas(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, titulo="Charla", contenido="Sesión de INFORMACIÓN para nuevos ingresos")
    publicar(autor, titulo="Otra", contenido="Nada que ver")
    for texto in ("informacion", "Información", "sesion"):
        assert _ids(buscar(client, autor, texto)) == [pub["id"]], texto


def test_raices_y_titulo(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    pub = publicar(autor, titulo="Talleres de robótica", contenido="Inscripciones abiertas")
    assert _ids(buscar(client, autor, "taller")) == [pub["id"]]  # singular encuentra plural
    assert _ids(buscar(client, autor, "robotica")) == [pub["id"]]
    assert _ids(buscar(client, autor, "inscrip")) == [pub["id"]]  # palabra a medio escribir
    extracto = buscar(client, autor, "inscripcion")["items"][0]["extracto"]
    assert [s["texto"] for s in extracto if s["resaltado"]] == ["Inscripciones"]


def test_frase_y_exclusion(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    ia = publicar(autor, contenido="Jornada de inteligencia artificial aplicada")
    otra = publicar(autor, contenido="La inteligencia emocional y lo artificial")
    assert _ids(buscar(client, autor, '"inteligencia artificial"')) == [ia["id"]]
    assert set(_ids(buscar(client, autor, "inteligencia"))) == {ia["id"], otra["id"]}
    assert _ids(buscar(client, autor, "inteligencia -emocional")) == [ia["id"]]


def test_respeta_la_audiencia(client: TestClient, usuario, publicar) -> None:
    profesor = usuario("profesor")
    estudiante = usuario("estudiante")
    publicar(profesor, contenido="Consejo de facultad extraordinario", audiencia_id=3)
    assert buscar(client, estudiante, "extraordinario")["total"] == 0
    assert buscar(client, {}, "extraordinario")["total"] == 0
    assert buscar(client, profesor, "extraordinario")["total"] == 1


def test_se_combina_con_filtros(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    teg = publicar(
        autor,
        contenido="Defensa sobre redes neuronales",
        categoria_id=categoria_id("Defensas de TEG"),
    )
    publicar(autor, contenido="Charla sobre redes neuronales")
    datos = buscar(client, autor, "redes", categoria_id=categoria_id("Defensas de TEG"))
    assert _ids(datos) == [teg["id"]]
    assert datos["total"] == 1


def test_resaltado_en_segmentos_de_texto(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    publicar(
        autor,
        contenido="Aviso <script>alert(1)</script> sobre la beca falsa de investigación",
    )
    extracto = buscar(client, autor, "beca")["items"][0]["extracto"]
    resaltados = [s["texto"] for s in extracto if s["resaltado"]]
    assert resaltados == ["beca"]
    completo = "".join(s["texto"] for s in extracto)
    # ts_headline quita las etiquetas que reconoce, pero no todo lo que parece HTML: por eso
    # la API nunca devuelve HTML y el cliente muestra los segmentos como texto.
    assert "alert(1)" in completo
    assert "" not in completo and "" not in completo  # no se falsifica el resaltado
    assert "falsa" in completo


def test_solo_palabras_vacias_y_busqueda_vacia(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    publicar(autor)
    res = client.get(PUBLICACIONES, headers=autor, params={"texto": "de la"})
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "texto"
    assert buscar(client, autor, "   ")["total"] == 1  # vacía: no se ejecuta la búsqueda
    res = client.get(PUBLICACIONES, headers=autor, params={"texto": "x"})
    assert res.status_code == 422
