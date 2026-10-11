"""Esquemas Pydantic (entrada/salida de la API) del módulo posts.

`AudienciaId`, `EstadoPublicacionId` y los esquemas de salida son públicos: los usarán
moderation (M4) y sharing (M6).
"""

from datetime import date, datetime
from enum import IntEnum, StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.core.pagination import CursorPage
from app.modules.identity.schemas import AutorOut


# Ids fijos de los catálogos (migración 0003). Único lugar donde el código Python los nombra.
class AudienciaId(IntEnum):
    PUBLICA = 1
    COMUNIDAD = 2
    SOLO_PROFESORES = 3
    SOLO_ESTUDIANTES = 4


class EstadoPublicacionId(IntEnum):
    VISIBLE = 1
    OCULTA_AUTOR = 2
    OCULTA_MODERACION = 3


class AccionModeracionId(IntEnum):
    REVISAR = 1
    OCULTAR = 2
    RESTAURAR = 3
    ELIMINAR = 4


class FiltroModeracion(StrEnum):
    """Pestañas de la bandeja (F4.1). Oculta por moderación implica revisada (CHECK)."""

    SIN_REVISAR = "sin_revisar"
    REVISADA = "revisada"
    OCULTA = "oculta"
    TODAS = "todas"


# ---------------------------------------------------------------- límites (F2.1, F2.4, F2.5)
TITULO_MIN, TITULO_MAX = 3, 150
CONTENIDO_MAX = 10_000
MAX_IMAGENES = 5
MAX_ETIQUETAS = 5
ETIQUETA_MAX = 30
TEXTO_MIN, TEXTO_MAX = 2, 100
EXTRACTO_MAX = 200
CONTEO_MAX = 1000

Titulo = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=TITULO_MIN, max_length=TITULO_MAX)
]
Contenido = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CONTENIDO_MAX)
]


# ---------------------------------------------------------------- entrada
class PublicacionIn(BaseModel):
    """Campos que escribe el autor. Autor, fecha y estado los pone el sistema (F2.1)."""

    titulo: Titulo
    contenido: Contenido
    categoria_id: int = Field(gt=0)
    audiencia_id: AudienciaId = AudienciaId.COMUNIDAD
    # Tal como las escribe el autor: el servicio las normaliza con f_normalizar_etiqueta
    # y el límite de 5 se cuenta después de quitar duplicados (F2.5).
    etiquetas: list[Annotated[str, StringConstraints(max_length=60)]] = Field(
        default_factory=list, max_length=20
    )


class FiltrosMuro(BaseModel):
    """Filtros del muro (F3.3-F3.5). Todos se combinan y actúan dentro de lo visible (F3.2)."""

    texto: str | None = Field(None, description="Palabras a buscar en título y contenido")
    categoria_id: int | None = Field(None, gt=0)
    autor_id: int | None = Field(None, gt=0)
    etiqueta: str | None = Field(None, max_length=60)
    desde: date | None = Field(None, description="Fecha local de Caracas, inclusive")
    hasta: date | None = Field(None, description="Fecha local de Caracas, inclusive")

    @field_validator("texto", "etiqueta", mode="before")
    @classmethod
    def _vacio_es_nada(cls, value: str | None) -> str | None:
        # F3.4: una búsqueda vacía no se ejecuta.
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("texto")
    @classmethod
    def _largo_texto(cls, value: str | None) -> str | None:
        if value is not None and not TEXTO_MIN <= len(value) <= TEXTO_MAX:
            raise ValueError(f"La búsqueda debe tener entre {TEXTO_MIN} y {TEXTO_MAX} caracteres")
        return value

    @field_validator("hasta")
    @classmethod
    def _rango(cls, value: date | None, info: ValidationInfo) -> date | None:
        # En el campo (no en el modelo) para que el error salga en `hasta`.
        desde = info.data.get("desde")
        if value and desde and desde > value:
            raise ValueError("La fecha final no puede ser anterior a la inicial")
        return value


class FiltrosBandeja(BaseModel):
    """Filtros de la bandeja de moderación (F4.1). Sin audiencia: el administrador ve todo."""

    estado: FiltroModeracion = FiltroModeracion.SIN_REVISAR
    categoria_id: int | None = Field(None, gt=0)
    autor_id: int | None = Field(None, gt=0)
    desde: date | None = Field(None, description="Fecha local de Caracas, inclusive")
    hasta: date | None = Field(None, description="Fecha local de Caracas, inclusive")

    @field_validator("hasta")
    @classmethod
    def _rango(cls, value: date | None, info: ValidationInfo) -> date | None:
        desde = info.data.get("desde")
        if value and desde and desde > value:
            raise ValueError("La fecha final no puede ser anterior a la inicial")
        return value


class VisibilidadIn(BaseModel):
    visible: bool = Field(description="false oculta la publicación; true la vuelve a mostrar")


# ---------------------------------------------------------------- salida
class CategoriaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str
    color: str


class CatalogoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    nombre: str


class CategoriaRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    color: str


class ImagenOut(BaseModel):
    id: int
    url: str
    orden: int = Field(description="1 es la portada")


class ModeracionOut(BaseModel):
    motivo: str | None
    ocultada_en: datetime


class PublicacionOut(BaseModel):
    id: int
    titulo: str
    contenido: str
    categoria: CategoriaRefOut
    audiencia: CatalogoOut
    estado: CatalogoOut
    autor: AutorOut
    imagenes: list[ImagenOut]
    etiquetas: list[str]
    publicada_en: datetime
    editada_en: datetime | None = Field(description='Marca "Editada" (F2.7)')
    es_autor: bool
    moderacion: ModeracionOut | None = Field(
        None, description="Motivo del ocultamiento; solo lo ven el autor y los administradores"
    )


class PublicacionResumenOut(BaseModel):
    """Fila de "mis publicaciones" (F2.8): sin el contenido completo."""

    id: int
    titulo: str
    categoria: CategoriaRefOut
    audiencia: CatalogoOut
    estado: CatalogoOut
    portada_url: str | None
    etiquetas: list[str]
    publicada_en: datetime
    editada_en: datetime | None


class SegmentoOut(BaseModel):
    """Trozo del extracto. Se muestra como texto plano; `resaltado` marca las coincidencias."""

    texto: str
    resaltado: bool = False


class TarjetaOut(BaseModel):
    """Tarjeta del muro (F3.1)."""

    id: int
    titulo: str
    extracto: list[SegmentoOut] = Field(
        description="Inicio del contenido o, al buscar, fragmentos con las palabras resaltadas"
    )
    portada_url: str | None
    categoria: CategoriaRefOut
    audiencia: CatalogoOut
    autor: AutorOut
    etiquetas: list[str]
    publicada_en: datetime
    editada_en: datetime | None


class MuroPage(CursorPage[TarjetaOut]):
    total: int | None = Field(
        None, description="Resultados de la búsqueda o los filtros; solo en la primera página"
    )
    total_supera: bool = Field(False, description=f"true: hay más de {CONTEO_MAX} resultados")


class AutorSugeridoOut(BaseModel):
    id: int
    nombre_completo: str


# ---------------------------------------------------------------- moderación (API para M4)
class FilaBandejaOut(BaseModel):
    """Fila de la bandeja de moderación (F4.1)."""

    id: int
    titulo: str
    extracto: list[SegmentoOut]
    portada_url: str | None
    categoria: CategoriaRefOut
    audiencia: CatalogoOut
    estado: CatalogoOut
    autor: AutorOut
    revisada: bool
    publicada_en: datetime
    editada_en: datetime | None
    ocultada_en: datetime | None = Field(description="Solo si está oculta por moderación")
    editada_tras_ocultar: bool = Field(
        description="El autor la editó después de que la ocultaran: conviene revisarla (F4.4)"
    )
    marca: str = Field(description="Se envía con cada acción para detectar cambios intermedios")


class EstadoModeracionOut(BaseModel):
    """Estado de moderación vigente de una publicación, para el detalle del M4."""

    revisada: bool
    marca: str


class InstantaneaModeracion(BaseModel):
    """Lo que el M4 guarda en el historial: la publicación tal como estaba al moderarla."""

    publicacion_id: int
    titulo: str
    autor_id: int
    eliminada: bool = False


# ---------------------------------------------------------------- categorías (M5, F5.7-F5.9)
COLOR_HEX = r"^#[0-9A-Fa-f]{6}$"


class CategoriaIn(BaseModel):
    """Nombre único sin distinguir mayúsculas (F5.7, HU-25).

    Al editar, `descripcion` y `color` omitidos conservan su valor: renombrar no borra el color
    de la categoría, que es el de su plantilla de tarjeta (F6.2).
    """

    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    descripcion: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = (
        None
    )
    color: Annotated[str, StringConstraints(pattern=COLOR_HEX)] | None = Field(
        None, description="#RRGGBB. Al crear, por defecto #1E40AF"
    )

    @field_validator("color")
    @classmethod
    def _mayusculas(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class EstadoCategoriaIn(BaseModel):
    activa: bool = Field(description="false la retira del formulario de publicación (F5.9)")


class CategoriaAdminOut(CategoriaOut):
    activa: bool
    creada_en: datetime
    publicaciones: int = Field(description="Publicaciones que la usan; por eso no se borra")


# -------------------------------------------------------- tarjeta para Instagram (API para M6)
class TarjetaDatos(BaseModel):
    """Lo que el M6 necesita para componer la tarjeta. No es la tarjeta del muro (TarjetaOut)."""

    publicacion: PublicacionOut
    portada_ruta: str | None = Field(description="Ruta en storage de la imagen de menor orden")
    version: datetime = Field(description="editada_en, o publicada_en si nunca se editó")
