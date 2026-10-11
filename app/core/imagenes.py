"""Validación y re-codificación de imágenes subidas por usuarios (fotos de perfil y publicaciones).

Toda imagen se abre con Pillow para comprobar que es real (no basta la extensión ni el MIME que
envía el navegador) y se vuelve a guardar como WEBP. Re-codificar quita EXIF, GPS y cualquier
dato escondido en el archivo original (guía de seguridad de Pillow).
"""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import AppError

IMAGEN_MAX_BYTES = 5 * 1024 * 1024  # F1.7 y F2.4
FORMATOS_ADMITIDOS = {"JPEG", "PNG", "WEBP"}
TIPO_MIME = "image/webp"
# Protege contra "bombas de descompresión": una imagen pequeña en bytes pero enorme en píxeles.
Image.MAX_IMAGE_PIXELS = 40_000_000


class ImagenInvalida(AppError):
    status_code, code, title = 422, "invalid_image", "La imagen no es válida"


def procesar_imagen(
    data: bytes, *, cuadrado: int | None = None, lado_max: int | None = None
) -> bytes:
    """Valida la imagen y la devuelve como WEBP sin metadatos.

    `cuadrado`: recorta al centro y deja un cuadrado de ese lado (foto de perfil).
    `lado_max`: reduce sin recortar hasta que el lado mayor mida como mucho eso (publicaciones).
    """
    if not data:
        raise ImagenInvalida("El archivo está vacío.")
    if len(data) > IMAGEN_MAX_BYTES:
        raise ImagenInvalida("La imagen supera el máximo de 5 MB.")
    try:
        with warnings.catch_warnings():
            # Entre 1x y 2x MAX_IMAGE_PIXELS Pillow solo avisa: aquí también es un rechazo.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.format not in FORMATOS_ADMITIDOS:
                    raise ImagenInvalida("Formato no admitido. Usa JPG, PNG o WEBP.")
                img.load()
                img = ImageOps.exif_transpose(img)
                modo = "RGBA" if img.mode in ("RGBA", "LA", "P", "PA") else "RGB"
                img = img.convert(modo)
                if cuadrado:
                    img = ImageOps.fit(img, (cuadrado, cuadrado))
                elif lado_max and max(img.size) > lado_max:
                    img.thumbnail((lado_max, lado_max))
                img.info = {}  # convert() copia info (exif, icc, xmp): no debe llegar al WEBP
                salida = io.BytesIO()
                img.save(salida, format="WEBP", quality=85)
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
    ) as exc:
        raise ImagenInvalida("El archivo no es una imagen válida.") from exc
    return salida.getvalue()
