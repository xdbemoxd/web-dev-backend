"""F2.8 (mis publicaciones) y F2.5 (sugerencias de etiquetas, D16)."""

from fastapi.testclient import TestClient

from tests.modules.posts.conftest import PUBLICACIONES

MIAS = "/api/v1/usuarios/me/publicaciones"
SUGERENCIAS = "/api/v1/etiquetas/sugerencias"


def test_mis_publicaciones_incluye_ocultas_y_pagina_por_cursor(
    client: TestClient, usuario, publicar
) -> None:
    autor = usuario()
    publicar(usuario("profesor"))  # ajena: no aparece
    ids = [
        publicar(autor, titulo=f"Aviso {i}", imagenes=1 if i == 0 else 0)["id"] for i in range(5)
    ]
    client.put(f"{PUBLICACIONES}/{ids[1]}/visibilidad", headers=autor, json={"visible": False})

    pagina = client.get(MIAS, headers=autor, params={"limite": 2}).json()
    assert [p["id"] for p in pagina["items"]] == [ids[4], ids[3]]
    assert pagina["siguiente"]

    # Una publicación nueva entre páginas no repite ni salta elementos (keyset, D15).
    publicar(autor, titulo="Nueva")
    vistos = [p["id"] for p in pagina["items"]]
    cursor = pagina["siguiente"]
    while cursor:
        pagina = client.get(MIAS, headers=autor, params={"limite": 2, "cursor": cursor}).json()
        vistos += [p["id"] for p in pagina["items"]]
        cursor = pagina["siguiente"]
    assert vistos == list(reversed(ids))

    por_id = {
        p["id"]: p for p in client.get(MIAS, headers=autor, params={"limite": 50}).json()["items"]
    }
    assert por_id[ids[1]]["estado"]["codigo"] == "oculta_autor"
    assert por_id[ids[0]]["portada_url"]
    assert por_id[ids[2]]["portada_url"] is None


def test_cursor_invalido(client: TestClient, usuario) -> None:
    res = client.get(MIAS, headers=usuario(), params={"cursor": "basura"})
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == "cursor"


def test_sugerencias_respetan_la_audiencia_y_el_estado(
    client: TestClient, usuario, publicar
) -> None:
    estudiante = usuario("estudiante")
    profesor = usuario("profesor")
    publicar(profesor, audiencia_id=3, etiquetas=["jornadas-profes"])
    publicar(profesor, audiencia_id=2, etiquetas=["jornadas2026"])
    oculta = publicar(profesor, etiquetas=["jornadas-oculta"])
    client.put(
        f"{PUBLICACIONES}/{oculta['id']}/visibilidad", headers=profesor, json={"visible": False}
    )

    def sugerir(headers: dict, prefijo: str) -> list[str]:
        res = client.get(SUGERENCIAS, headers=headers, params={"prefijo": prefijo})
        assert res.status_code == 200
        return res.json()

    assert sugerir(estudiante, "Jorn") == ["jornadas2026"]
    assert sugerir(profesor, "jorn") == ["jornadas-profes", "jornadas2026"]
    assert sugerir(estudiante, "%") == []  # sin letras: nada, y sin comodines de LIKE
    assert client.get(SUGERENCIAS, params={"prefijo": "jorn"}).status_code == 401
