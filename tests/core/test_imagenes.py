"""Validación y re-codificación de imágenes (F1.7, F2.4)."""

import io

import pytest
from PIL import Image

from app.core.imagenes import ImagenInvalida, procesar_imagen
from tests.helpers import imagen


def _abrir(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_recodifica_a_webp_sin_metadatos() -> None:
    img = _abrir(procesar_imagen(imagen("JPEG", exif=b"Exif\x00\x00gps")))
    assert img.format == "WEBP"
    assert "exif" not in img.info


def test_reduce_sin_recortar_o_recorta_cuadrado() -> None:
    assert _abrir(procesar_imagen(imagen(tamano=(4000, 1000)), lado_max=2048)).size == (2048, 512)
    assert _abrir(procesar_imagen(imagen(tamano=(800, 600)), lado_max=2048)).size == (800, 600)
    assert _abrir(procesar_imagen(imagen(tamano=(800, 600)), cuadrado=512)).size == (512, 512)


def test_conserva_transparencia() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(buffer, format="PNG")
    assert _abrir(procesar_imagen(buffer.getvalue())).mode == "RGBA"


@pytest.mark.parametrize(
    "data",
    [b"", b"texto", b"\x00" * (5 * 1024 * 1024 + 1), imagen("GIF"), imagen("BMP")],
    ids=["vacio", "no-imagen", "grande", "gif", "bmp"],
)
def test_rechaza(data: bytes) -> None:
    with pytest.raises(ImagenInvalida):
        procesar_imagen(data)


def test_rechaza_bomba_de_descompresion() -> None:
    # 7000 x 7000 = 49 Mpx > 40 Mpx: comprimida pesa poco, descomprimida no.
    buffer = io.BytesIO()
    Image.new("1", (7000, 7000)).save(buffer, format="PNG")
    assert len(buffer.getvalue()) < 5 * 1024 * 1024
    with pytest.raises(ImagenInvalida):
        procesar_imagen(buffer.getvalue())
