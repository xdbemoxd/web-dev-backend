"""F3.1-F3.3, F3.5, F3.7 · HU-13, HU-14, HU-17."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_engine
from tests.helpers import sql
from tests.modules.posts.conftest import PUBLICACIONES, categoria_id
from tests.modules.posts.test_detalle import MATRIZ, ocultar_por_moderacion

AUTORES = "/api/v1/autores/sugerencias"


def fijar_fecha(pub_id: int, instante: str) -> None:
    """publicada_en es inmutable por trigger: en pruebas se salta con replication_role."""
    with get_engine().begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text("UPDATE publicacion SET publicada_en = CAST(:t AS timestamptz) WHERE id = :id"),
            {"t": instante, "id": pub_id},
        )


def _ids(res) -> list[int]:
    assert res.status_code == 200, res.text
    return [p["id"] for p in res.json()["items"]]


def test_muro_por_fecha_con_tarjetas(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    viejo = publicar(autor, titulo="Antiguo", contenido="palabra " * 100, imagenes=1)
    nuevo = publicar(autor, titulo="Reciente", etiquetas=["ia"])
    fijar_fecha(viejo["id"], "2026-01-10T12:00:00Z")
    res = client.get(PUBLICACIONES, headers=autor)
    datos = res.json()
    assert _ids(res) == [nuevo["id"], viejo["id"]]
    assert datos["total"] == 2
    tarjeta = datos["items"][1]
    assert tarjeta["portada_url"]
    assert tarjeta["autor"]["nombre_completo"] == "Ana Pérez"
    extracto = tarjeta["extracto"][0]["texto"]
    assert len(extracto) <= 202 and extracto.endswith("…")
    assert "contenido" not in tarjeta
    assert datos["items"][0]["etiquetas"] == ["ia"]


def test_muro_vacio(client: TestClient) -> None:
    datos = client.get(PUBLICACIONES).json()
    assert datos == {"items": [], "siguiente": None, "total": 0, "total_supera": False}


def test_ocultas_no_salen_ni_para_su_autor(client: TestClient, usuario, publicar) -> None:
    autor = usuario("profesor")
    admin = usuario("administrador")
    visible = publicar(autor)
    oculta = publicar(autor)
    moderada = publicar(autor)
    client.put(
        f"{PUBLICACIONES}/{oculta['id']}/visibilidad", headers=autor, json={"visible": False}
    )
    ocultar_por_moderacion(moderada["id"])
    for headers in (autor, admin):
        assert _ids(client.get(PUBLICACIONES, headers=headers)) == [visible["id"]]


@pytest.mark.parametrize("audiencia", [1, 2, 3, 4])
def test_matriz_de_audiencia_en_el_muro(
    client: TestClient, usuario, publicar, audiencia: int
) -> None:
    pub = publicar(usuario("profesor", "autor@facyt.uc.edu.ve"), audiencia_id=audiencia)
    lectores = {
        "visitante": {},
        "estudiante": usuario("estudiante"),
        "profesor": usuario("profesor"),
        "administrador": usuario("administrador"),
    }
    for lector, headers in lectores.items():
        visto = pub["id"] in _ids(client.get(PUBLICACIONES, headers=headers))
        assert visto == (lector in MATRIZ[audiencia]), (audiencia, lector)


def test_autor_ve_lo_suyo_aunque_su_rol_no_lo_permita(
    client: TestClient, usuario, publicar
) -> None:
    estudiante = usuario()
    pub = publicar(estudiante, audiencia_id=3)  # Solo profesores (HU-13)
    assert _ids(client.get(PUBLICACIONES, headers=estudiante)) == [pub["id"]]
    assert _ids(client.get(PUBLICACIONES, headers=usuario("estudiante", "b@facyt.uc.edu.ve"))) == []


def test_cursor_sin_repetidos_ni_huecos(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    ids = [publicar(autor, titulo=f"Aviso {i}")["id"] for i in range(7)]
    primera = client.get(PUBLICACIONES, params={"limite": 3}, headers=autor).json()
    assert primera["total"] == 7
    vistos = [p["id"] for p in primera["items"]]
    publicar(autor, titulo="Llega en medio")
    cursor = primera["siguiente"]
    while cursor:
        pagina = client.get(PUBLICACIONES, params={"limite": 3, "cursor": cursor}, headers=autor)
        datos = pagina.json()
        assert datos["total"] is None  # solo en la primera página
        vistos += [p["id"] for p in datos["items"]]
        cursor = datos["siguiente"]
    assert vistos == list(reversed(ids))


def test_filtro_categoria_y_fechas_en_caracas(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    teg = categoria_id("Defensas de TEG")
    en_rango = publicar(autor, categoria_id=teg)
    borde = publicar(autor, categoria_id=teg)
    fuera = publicar(autor, categoria_id=teg)
    otra_categoria = publicar(autor)
    fijar_fecha(en_rango["id"], "2026-09-10T15:00:00Z")
    # 30/09 23:30 en Caracas (UTC-4) = 01/10 03:30 UTC: entra con hasta=2026-09-30.
    fijar_fecha(borde["id"], "2026-10-01T03:30:00Z")
    fijar_fecha(fuera["id"], "2026-10-01T04:30:00Z")  # 01/10 00:30 en Caracas
    fijar_fecha(otra_categoria["id"], "2026-09-15T12:00:00Z")
    res = client.get(
        PUBLICACIONES,
        headers=autor,
        params={"categoria_id": teg, "desde": "2026-09-01", "hasta": "2026-09-30"},
    )
    assert _ids(res) == [borde["id"], en_rango["id"]]
    assert res.json()["total"] == 2


def test_categoria_desactivada_sigue_filtrando(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    logros = categoria_id("Logros")
    pub = publicar(autor, categoria_id=logros)
    sql("UPDATE categoria SET activa = false WHERE id = :id", id=logros)
    assert _ids(client.get(PUBLICACIONES, headers=autor, params={"categoria_id": logros})) == [
        pub["id"]
    ]
    todas = client.get("/api/v1/categorias", params={"incluir_inactivas": True}).json()
    assert "Logros" in [c["nombre"] for c in todas]


def test_filtro_autor_y_etiqueta(client: TestClient, usuario, publicar) -> None:
    ana = usuario()
    luis = usuario("profesor")
    de_ana = publicar(ana, etiquetas=["Jornadas 2026"])
    publicar(luis, etiquetas=["jornadas2026"])
    autor_id = de_ana["autor"]["id"]
    assert _ids(client.get(PUBLICACIONES, headers=ana, params={"autor_id": autor_id})) == [
        de_ana["id"]
    ]
    res = client.get(PUBLICACIONES, headers=ana, params={"etiqueta": "Jornadas 2026"})
    assert len(_ids(res)) == 2
    res = client.get(
        PUBLICACIONES, headers=ana, params={"etiqueta": "jornadas2026", "autor_id": autor_id}
    )
    assert _ids(res) == [de_ana["id"]]
    assert _ids(client.get(PUBLICACIONES, headers=ana, params={"etiqueta": "noexiste"})) == []


@pytest.mark.parametrize(
    ("params", "campo"),
    [
        ({"desde": "2026-10-01", "hasta": "2026-09-01"}, "hasta"),
        ({"etiqueta": "!!!"}, "etiqueta"),
        ({"cursor": "basura"}, "cursor"),
        ({"limite": 51}, "limite"),
    ],
)
def test_filtros_invalidos(client: TestClient, params: dict, campo: str) -> None:
    res = client.get(PUBLICACIONES, params=params)
    assert res.status_code == 422
    assert [e["field"] for e in res.json()["errors"]] == [campo]


def test_conteo_con_tope(client: TestClient, usuario, publicar) -> None:
    autor = usuario()
    base = publicar(autor, audiencia_id=1)
    sql(
        "INSERT INTO publicacion (autor_id, categoria_id, audiencia_id, titulo, contenido) "
        "SELECT autor_id, categoria_id, 1, 'Masiva ' || g, 'contenido' "
        "FROM publicacion, generate_series(1, 1005) g WHERE id = :id",
        id=base["id"],
    )
    datos = client.get(PUBLICACIONES).json()
    assert datos["total"] == 1000
    assert datos["total_supera"] is True
    assert len(datos["items"]) == 20


def test_sugerir_autores(client: TestClient, registrar, login, usuario, publicar) -> None:
    registrar("jose@facyt.uc.edu.ve", nombre_completo="José Pérez")
    jose = login("jose@facyt.uc.edu.ve")
    registrar("josefina@facyt.uc.edu.ve", nombre_completo="Josefina 100% Real")
    publicar(jose, audiencia_id=3)  # Solo profesores
    lector = usuario("profesor")
    estudiante = usuario("estudiante")

    def sugerir(headers: dict, nombre: str) -> list[str]:
        res = client.get(AUTORES, headers=headers, params={"nombre": nombre})
        assert res.status_code == 200, res.text
        return [a["nombre_completo"] for a in res.json()]

    assert sugerir(lector, "JOSE") == ["José Pérez"]  # Josefina no tiene publicaciones
    assert sugerir(estudiante, "jose") == []  # no puede ver sus publicaciones
    assert sugerir({}, "jose") == []  # el visitante solo ve autores de públicas
    publicar(jose, audiencia_id=1)
    assert sugerir({}, "josé") == ["José Pérez"]
    assert sugerir(lector, "%%") == []  # % y _ no son comodines
    assert sugerir(lector, "__") == []
    assert sugerir(lector, "0%") == []
    sql("UPDATE usuario SET activo = false WHERE correo = 'jose@facyt.uc.edu.ve'")
    assert sugerir(lector, "jose") == []
