"""Composición de la tarjeta para Instagram con Pillow (F6.2). Privado del módulo sharing.

Funciones puras: reciben los datos ya validados y devuelven el JPEG. Sin navegador (Chromium no
cabe en los 512 MB de Render) y sin red: la portada llega como bytes leídos del storage propio.
Las fuentes son las del sistema de diseño (OFL 1.1, ver fonts/LICENSE-*).
"""

import io
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont, ImageOps

LADO = 1080
MARGEN = 72
ANCHO_UTIL = LADO - 2 * MARGEN
TITULO_PX, TITULO_LINEAS, TITULO_INTERLINEA = 64, 3, 1.14
QR_PX = 156
CALIDAD_JPEG = 88
PLATAFORMA = "Red FaCyT"
TINTA = (11, 18, 32)  # casi negro azulado: el fg del sistema de diseño
BLANCO = (255, 255, 255)
SECUNDARIO = (222, 228, 238)  # texto secundario claro: ≥ 4.5:1 sobre el velo
CARACAS = timezone(timedelta(hours=-4))  # Venezuela no tiene horario de verano
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic")

FUENTES = Path(__file__).parent / "fonts"


@dataclass(frozen=True)
class Contenido:
    titulo: str
    categoria: str
    color: str  # "#RRGGBB"
    autor: str
    fecha: datetime
    enlace: str  # URL completa a la publicación: va en el QR
    enlace_visible: str  # la misma, sin "https://": va escrita


# ---------------------------------------------------------------- texto
def limpiar(texto: str) -> str:
    """NFC (tildes como un solo carácter), sin emoji ni símbolos que la fuente no tiene, y con
    los espacios colapsados. Las fuentes cubren el latín, que es lo que se escribe en la FaCyT."""
    limpio = []
    for c in unicodedata.normalize("NFC", texto):
        if ord(c) > 0xFFFF or unicodedata.category(c) in ("So", "Cs", "Co", "Cn", "Cf"):
            continue
        if 0xFE00 <= ord(c) <= 0xFE0F:  # selectores de variación de emoji
            continue
        # Tras NFC, una marca combinante que sigue sin letra base quedaría flotando.
        if unicodedata.category(c) == "Mn" and (not limpio or not limpio[-1].isalpha()):
            continue
        limpio.append(c)
    return " ".join("".join(limpio).split())


def envolver(texto: str, fuente: ImageFont.FreeTypeFont, ancho: int, max_lineas: int) -> list[str]:
    """Reparte por ancho real en píxeles y, si no cabe, termina la última línea en "…"."""
    medir = fuente.getlength
    lineas: list[str] = []
    actual = ""
    for palabra in texto.split(" "):
        # Una palabra más ancha que la línea (una URL, por ejemplo) se parte por caracteres.
        while medir(palabra) > ancho:
            corte = len(palabra)
            while corte > 1 and medir(palabra[:corte]) > ancho:
                corte -= 1
            if actual:
                lineas.append(actual)
                actual = ""
            lineas.append(palabra[:corte])
            palabra = palabra[corte:]
        prueba = f"{actual} {palabra}".strip()
        if medir(prueba) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    if len(lineas) <= max_lineas:
        return lineas
    ultima = lineas[max_lineas - 1]
    while ultima and medir(ultima + "…") > ancho:
        ultima = ultima[:-1]
    return [*lineas[: max_lineas - 1], ultima.rstrip(" ,.;:-") + "…"]


def fecha_corta(fecha: datetime) -> str:
    local = (fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)).astimezone(CARACAS)
    return f"{local.day} {MESES[local.month - 1]} {local.year}"


@lru_cache(maxsize=16)
def _fuente_bytes(nombre: str) -> bytes:
    return (FUENTES / nombre).read_bytes()


def fuente(nombre: str, tamano: int, peso: int) -> ImageFont.FreeTypeFont:
    """Una instancia por tarjeta: FreeType no es seguro de compartir entre hilos."""
    f = ImageFont.truetype(io.BytesIO(_fuente_bytes(nombre)), tamano)
    f.set_variation_by_axes([peso])
    return f


# ---------------------------------------------------------------- color y contraste (WCAG)
def hex_a_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _luminancia(rgb: tuple[int, int, int]) -> float:
    def canal(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def texto_sobre(fondo: tuple[int, int, int]) -> tuple[int, int, int]:
    """Blanco o tinta, el que más contraste tenga sobre el color de la categoría."""
    return BLANCO if contraste(BLANCO, fondo) >= contraste(TINTA, fondo) else TINTA


def fondo_legible(color: tuple[int, int, int], minimo: float = 5.5) -> tuple[int, int, int]:
    """Oscurece el color hacia la tinta hasta que el texto blanco cumpla el contraste."""
    for paso in range(21):
        t = paso / 20
        mezcla = tuple(round(c * (1 - t) + k * t) for c, k in zip(color, TINTA, strict=True))
        if contraste(BLANCO, mezcla) >= minimo:  # type: ignore[arg-type]
            return mezcla  # type: ignore[return-value]
    return TINTA


# ---------------------------------------------------------------- piezas
def _mascara_redondeada(tamano: tuple[int, int], radio: int) -> Image.Image:
    """Dibujada a 4x y reducida: los bordes de rounded_rectangle no tienen antialias."""
    escala = 4
    grande = Image.new("L", (tamano[0] * escala, tamano[1] * escala), 0)
    ImageDraw.Draw(grande).rounded_rectangle(
        (0, 0, grande.width - 1, grande.height - 1), radius=radio * escala, fill=255
    )
    return grande.resize(tamano, Image.Resampling.LANCZOS)


def _fondo_con_portada(portada: bytes, y_texto: int) -> Image.Image:
    """La portada ocupa todo el cuadro y un velo de tinta cubre desde un poco antes del texto.

    Sobre una foto blanca, el texto blanco necesita al menos 56 % de velo para 4.5:1; debajo del
    texto se usa 80 % y crece hacia el pie, así el contraste no depende de la foto.
    """
    with Image.open(io.BytesIO(portada), formats=["WEBP", "JPEG", "PNG"]) as img:
        img.draft("RGB", (LADO, LADO))  # JPEG grandes se decodifican ya reducidos
        base = ImageOps.fit(img.convert("RGB"), (LADO, LADO), Image.Resampling.LANCZOS)
    inicio = max(0, y_texto - 220)
    alfa = Image.new("L", (1, LADO), 0)
    for y in range(LADO):
        if y < 240:  # velo arriba para la marca: legible aun sobre una foto blanca
            v = 0.75 * (1 - y / 240) ** 1.2
        elif y < inicio:
            v = 0.0
        elif y < y_texto:
            v = 0.8 * ((y - inicio) / (y_texto - inicio)) ** 1.4
        else:
            v = 0.8 + 0.14 * (y - y_texto) / max(1, LADO - y_texto)
        alfa.putpixel((0, y), round(v * 255))
    velo = Image.new("RGBA", (LADO, LADO), (*TINTA, 255))
    velo.putalpha(alfa.resize((LADO, LADO)))
    return Image.alpha_composite(base.convert("RGBA"), velo)


def _fondo_plantilla(color: tuple[int, int, int], categoria: str) -> Image.Image:
    """Sin imagen: el color de la categoría con papel milimetrado ("Laboratorio vivo") y el
    nombre de la categoría grande. El color nunca va solo: siempre con su nombre (MASTER)."""
    img = Image.new("RGBA", (LADO, LADO), (*fondo_legible(color), 255))
    rejilla = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    d = ImageDraw.Draw(rejilla)
    for i, x in enumerate(range(0, LADO + 1, 36)):
        alfa = 34 if i % 5 == 0 else 14
        d.line((x, 0, x, LADO), fill=(255, 255, 255, alfa), width=1)
        d.line((0, x, LADO, x), fill=(255, 255, 255, alfa), width=1)
    img = Image.alpha_composite(img, rejilla)
    grande = fuente("space-grotesk.woff2", 104, 700)
    capa = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    y = 190
    for linea in envolver(limpiar(categoria), grande, ANCHO_UTIL, 2):
        d.text((MARGEN - 4, y), linea, font=grande, fill=(255, 255, 255, 56))
        y += 112
    return Image.alpha_composite(img, capa)


def _chip(texto: str, fondo: tuple[int, int, int], tinta: tuple[int, int, int]) -> Image.Image:
    f = fuente("dm-sans.woff2", 30, 700)
    ancho = min(int(f.getlength(texto)) + 62, ANCHO_UTIL)
    alto = 54
    chip = Image.new("RGBA", (ancho, alto), (*fondo, 255))
    d = ImageDraw.Draw(chip)
    d.ellipse((20, alto // 2 - 6, 32, alto // 2 + 6), fill=tinta)
    texto = envolver(texto, f, ancho - 62, 1)[0]
    d.text((42, alto // 2), texto, font=f, fill=tinta, anchor="lm")
    chip.putalpha(_mascara_redondeada((ancho, alto), alto // 2))
    return chip


def _qr(url: str) -> Image.Image:
    codigo = segno.make(url, error="m")
    modulos = codigo.symbol_size(scale=1, border=2)[0]
    buf = io.BytesIO()
    codigo.save(buf, kind="png", scale=max(1, (QR_PX - 24) // modulos), border=2, dark=TINTA)
    buf.seek(0)
    with Image.open(buf) as img:
        qr = img.convert("RGB")
    lado = qr.width + 24
    marco = Image.new("RGBA", (lado, lado), (*BLANCO, 255))
    marco.paste(qr, (12, 12))
    marco.putalpha(_mascara_redondeada((lado, lado), 18))
    return marco


# ---------------------------------------------------------------- composición
def componer(contenido: Contenido, portada: bytes | None) -> bytes:
    """1080 x 1080: portada (o plantilla), categoría, título, fecha y autor, marca, enlace y QR.

    Primero se calcula dónde va el texto (de abajo hacia arriba) y con eso el velo del fondo.
    """
    color = hex_a_rgb(contenido.color)
    qr = _qr(contenido.enlace)
    y_qr = LADO - MARGEN - qr.height

    meta_f = fuente("dm-sans.woff2", 30, 500)
    meta = envolver(
        f"{fecha_corta(contenido.fecha)}  ·  {limpiar(contenido.autor)}", meta_f, ANCHO_UTIL, 1
    )[0]
    y_meta = y_qr - 70

    titulo_f = fuente("space-grotesk.woff2", TITULO_PX, 700)
    lineas = envolver(limpiar(contenido.titulo), titulo_f, ANCHO_UTIL, TITULO_LINEAS)
    paso = round(TITULO_PX * TITULO_INTERLINEA)
    y_titulo = y_meta - 30 - paso * len(lineas)

    # Con portada el chip lleva el color de la categoría; en la plantilla el fondo ya es ese
    # color, así que el chip se invierte (claro con texto del color) para no desaparecer.
    if portada:
        chip = _chip(limpiar(contenido.categoria), color, texto_sobre(color))
    else:
        chip = _chip(limpiar(contenido.categoria), BLANCO, fondo_legible(color))
    y_chip = y_titulo - chip.height - 26

    lienzo = (
        _fondo_con_portada(portada, y_chip - 20)
        if portada
        else _fondo_plantilla(color, contenido.categoria)
    )
    d = ImageDraw.Draw(lienzo)

    # Marca arriba a la izquierda, con el "núcleo" ámbar del logo.
    marca = fuente("space-grotesk.woff2", 38, 700)
    d.ellipse((MARGEN, MARGEN + 12, MARGEN + 18, MARGEN + 30), fill=(245, 176, 66))
    d.text((MARGEN + 30, MARGEN + 21), PLATAFORMA, font=marca, fill=BLANCO, anchor="lm")

    lienzo.alpha_composite(chip, (MARGEN, y_chip))
    y = y_titulo
    for linea in lineas:
        d.text((MARGEN, y), linea, font=titulo_f, fill=BLANCO)
        y += paso
    d.text((MARGEN, y_meta), meta, font=meta_f, fill=SECUNDARIO)

    # Pie: enlace escrito y QR (F6.2: "el enlace corto a la publicación").
    lienzo.alpha_composite(qr, (LADO - MARGEN - qr.width, y_qr))
    ancho_texto = ANCHO_UTIL - qr.width - 32
    base_enlace = y_qr + qr.height - 14
    invitacion = fuente("dm-sans.woff2", 28, 500)
    d.text(
        (MARGEN, base_enlace - 48),
        "Léela completa en",
        font=invitacion,
        fill=SECUNDARIO,
        anchor="ls",
    )
    enlace_f = fuente("dm-sans.woff2", 34, 700)
    visible = envolver(contenido.enlace_visible, enlace_f, ancho_texto, 1)[0]
    d.text((MARGEN, base_enlace), visible, font=enlace_f, fill=BLANCO, anchor="ls")

    salida = io.BytesIO()
    lienzo.convert("RGB").save(salida, "JPEG", quality=CALIDAD_JPEG, optimize=True, subsampling=0)
    return salida.getvalue()
