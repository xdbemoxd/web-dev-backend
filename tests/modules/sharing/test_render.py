"""Composición de la tarjeta (F6.2), sin HTTP."""

import io
import time
from datetime import UTC, datetime

import segno
from PIL import Image

from app.modules.sharing import render
from app.modules.sharing.service import enlace, nombre_archivo


def _contenido(**cambios) -> render.Contenido:
    datos = dict(
        titulo="Taller de Python",
        categoria="Talleres, charlas y jornadas",
        color="#C2410C",
        autor="María José Pérez",
        fecha=datetime(2026, 9, 29, 2, 30, tzinfo=UTC),  # 28 sept 22:30 en Caracas
        enlace="https://red-facyt.vercel.app/p/7",
        enlace_visible="red-facyt.vercel.app/p/7",
    )
    datos.update(cambios)
    return render.Contenido(**datos)


def test_titulo_largo_en_tres_lineas_con_puntos_suspensivos() -> None:
    f = render.fuente("space-grotesk.woff2", render.TITULO_PX, 700)
    titulo = "Jornada de investigación " * 12
    lineas = render.envolver(titulo, f, render.ANCHO_UTIL, render.TITULO_LINEAS)
    assert len(lineas) == 3
    assert lineas[-1].endswith("…")
    assert all(f.getlength(linea) <= render.ANCHO_UTIL for linea in lineas)
    assert render.envolver("Corto", f, render.ANCHO_UTIL, 3) == ["Corto"]


def test_palabra_mas_ancha_que_la_linea_se_parte() -> None:
    f = render.fuente("dm-sans.woff2", 34, 700)
    lineas = render.envolver("a" * 200, f, 300, 5)
    assert all(f.getlength(linea) <= 300 for linea in lineas)


def test_limpiar_conserva_tildes_y_quita_emoji() -> None:
    assert render.limpiar("Ñandú  ́ ¿Qué?  🚀🐍 ok️") == "Ñandú ¿Qué? ok"
    assert render.limpiar("Café") == "Café"  # NFC


def test_texto_del_chip_segun_contraste() -> None:
    assert render.texto_sobre(render.hex_a_rgb("#1E40AF")) == render.BLANCO
    assert render.texto_sobre(render.hex_a_rgb("#FDE68A")) == render.TINTA
    for color in ("#A16207", "#15803D", "#4B5563", "#FDE68A"):
        fondo = render.fondo_legible(render.hex_a_rgb(color))
        assert render.contraste(render.BLANCO, fondo) >= 5.5


def test_fecha_en_hora_de_caracas() -> None:
    assert render.fecha_corta(datetime(2026, 9, 29, 2, 30, tzinfo=UTC)) == "28 sept 2026"


def test_componer_con_y_sin_portada_rapido() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (2048, 1365), (240, 240, 240)).save(buf, "WEBP")
    inicio = time.perf_counter()
    for portada in (buf.getvalue(), None):
        img = Image.open(io.BytesIO(render.componer(_contenido(titulo="Título ñ 🚀"), portada)))
        assert img.size == (1080, 1080)
        # Detrás del título el velo oscurece la foto clara: el texto blanco se lee (≥ 4.5:1).
        if portada:
            assert render.contraste(render.BLANCO, img.getpixel((1060, 760))) >= 4.5
    assert time.perf_counter() - inicio < 3  # dos tarjetas; en Render cada una < 1.5 s


def test_qr_y_nombre_de_archivo() -> None:
    url, visible = enlace(42)
    assert url.endswith("/p/42") and "://" not in visible
    assert segno.make(url, error="m").designator  # el QR codifica la URL de la publicación
    assert nombre_archivo("¡Defensa de TEG: Química!") == "red-facyt-defensa-de-teg-quimica.jpg"
    assert nombre_archivo("🚀🚀") == "red-facyt-publicacion.jpg"
    assert len(nombre_archivo("a" * 300)) <= len("red-facyt-.jpg") + 60
    largo = nombre_archivo("Jornadas de Investigación FaCyT 2026: ponencias de Química y Física")
    assert largo == "red-facyt-jornadas-de-investigacion-facyt-2026-ponencias-de-quimica-y.jpg"
