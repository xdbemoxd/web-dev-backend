"""Acceso a datos del módulo posts: consultas SQLAlchemy. Privado.

No hace commit: la transacción la controla el servicio. La visibilidad por audiencia se decide
siempre en SQL con `audiencia_visible_para` (modelo-de-datos.md §6, D3).
"""

from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    Date,
    DateTime,
    Integer,
    Text,
    cast,
    func,
    literal_column,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.pagination import Cursor
from app.modules.posts.models import Categoria, Etiqueta, Publicacion, PublicacionEtiqueta
from app.modules.posts.schemas import (
    CONTEO_MAX,
    EstadoPublicacionId,
    FiltroModeracion,
    FiltrosBandeja,
    FiltrosMuro,
)

ZONA = "America/Caracas"  # las fechas de los filtros son locales (UTC-4)
MARCA_INICIO, MARCA_FIN = "", ""  # uso privado de Unicode: no es HTML


# ---------------------------------------------------------------- categorías (F2.2, F3.3)
def categorias(db: Session, incluir_inactivas: bool = False) -> list[Categoria]:
    stmt = select(Categoria).order_by(Categoria.nombre)
    if not incluir_inactivas:
        stmt = stmt.where(Categoria.activa)
    return list(db.scalars(stmt))


def categoria_activa(db: Session, categoria_id: int) -> bool:
    return bool(db.scalar(select(Categoria.activa).where(Categoria.id == categoria_id)) or False)


# ---------------------------------------------------------------- publicaciones
def get_publicacion(db: Session, publicacion_id: int) -> Publicacion | None:
    return db.get(Publicacion, publicacion_id)


def get_para_modificar(db: Session, publicacion_id: int) -> Publicacion | None:
    """Bloquea la fila: dos ediciones simultáneas no se pisan los órdenes de imágenes."""
    return db.scalars(
        select(Publicacion).where(Publicacion.id == publicacion_id).with_for_update(of=Publicacion)
    ).one_or_none()


def _audiencia_visible(rol_id: int | None):
    # CAST explícito: con un visitante el rol es NULL y Postgres necesita saber su tipo.
    return func.audiencia_visible_para(Publicacion.audiencia_id, cast(rol_id, Integer))


def _visible_para(rol_id: int | None, yo: int | None):
    """Regla F3.2: la audiencia lo permite, o es el autor (siempre ve lo suyo)."""
    return or_(_audiencia_visible(rol_id), Publicacion.autor_id == yo)


def detalle_visible(
    db: Session, publicacion_id: int, rol_id: int | None, yo: int | None, es_admin: bool
) -> Publicacion | None:
    """Detalle (F3.6): visibles de su audiencia; el autor ve las suyas y el admin todas."""
    stmt = select(Publicacion).where(Publicacion.id == publicacion_id)
    if not es_admin:
        stmt = stmt.where(
            or_(
                (Publicacion.estado_id == EstadoPublicacionId.VISIBLE) & _audiencia_visible(rol_id),
                Publicacion.autor_id == yo,
            )
        )
    return db.scalars(stmt).one_or_none()


def existe(db: Session, publicacion_id: int) -> bool:
    return db.scalar(select(Publicacion.id).where(Publicacion.id == publicacion_id)) is not None


def mis_publicaciones(
    db: Session, autor_id: int, cursor: Cursor | None, limite: int
) -> list[Publicacion]:
    """Todas las del autor, también ocultas (F2.8). Usa ix_publicacion_autor_fecha."""
    stmt = select(Publicacion).where(Publicacion.autor_id == autor_id)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Publicacion.publicada_en, Publicacion.id) < (cursor.fecha, cursor.id)
        )
    stmt = stmt.order_by(Publicacion.publicada_en.desc(), Publicacion.id.desc()).limit(limite + 1)
    return list(db.scalars(stmt))


def motivo_moderacion(db: Session, publicacion_id: int) -> tuple[str | None, datetime] | None:
    fila = db.execute(
        text(
            "SELECT motivo_ocultamiento, ocultada_en FROM v_publicacion_moderacion "
            "WHERE publicacion_id = :id"
        ),
        {"id": publicacion_id},
    ).one_or_none()
    return (fila.motivo_ocultamiento, fila.ocultada_en) if fila else None


def diferir_orden(db: Session) -> None:
    """Los UNIQUE de orden/posición se comprueban al commit: permite reordenar (D7)."""
    db.execute(
        text(
            "SET CONSTRAINTS uq_imagen_publicacion_publicacion_id_orden, "
            "uq_publicacion_etiqueta_publicacion_id_posicion DEFERRED"
        )
    )


# ---------------------------------------------------------------- etiquetas (F2.5)
def normalizar_etiquetas(db: Session, textos: list[str]) -> list[str | None]:
    """Aplica f_normalizar_etiqueta, la única regla de normalización (D16), en orden."""
    if not textos:
        return []
    filas = db.execute(
        text(
            "SELECT public.f_normalizar_etiqueta(t) AS nombre "
            "FROM unnest(CAST(:textos AS text[])) WITH ORDINALITY AS x(t, n) ORDER BY n"
        ),
        {"textos": textos},
    ).all()
    return [fila.nombre for fila in filas]


def asegurar_etiquetas(db: Session, nombres: list[str]) -> dict[str, Etiqueta]:
    if not nombres:
        return {}
    db.execute(
        insert(Etiqueta)
        .values([{"nombre": n} for n in nombres])
        .on_conflict_do_nothing(index_elements=["nombre"])
    )
    etiquetas = db.scalars(select(Etiqueta).where(Etiqueta.nombre.in_(nombres)))
    return {e.nombre: e for e in etiquetas}


def sugerir_etiquetas(db: Session, prefijo: str, rol_id: int | None, yo: int | None) -> list[str]:
    """Solo etiquetas de publicaciones que quien escribe puede ver (modelo-de-datos.md §7).

    `prefijo` llega ya normalizado (solo a-z, 0-9, ñ y guiones: no trae comodines de LIKE), así
    el patrón es constante y la búsqueda usa el índice único de `nombre` (COLLATE "C").
    """
    visible = (
        select(PublicacionEtiqueta.etiqueta_id)
        .join(Publicacion, Publicacion.id == PublicacionEtiqueta.publicacion_id)
        .where(
            PublicacionEtiqueta.etiqueta_id == Etiqueta.id,
            Publicacion.estado_id == EstadoPublicacionId.VISIBLE,
            _visible_para(rol_id, yo),
        )
    )
    stmt = (
        select(Etiqueta.nombre)
        .where(
            Etiqueta.nombre.like(f"{prefijo}%"),
            visible.exists(),
        )
        .order_by(Etiqueta.nombre)
        .limit(10)
    )
    return list(db.scalars(stmt))


# ---------------------------------------------------------------- muro y búsqueda (F3.1-F3.5)
# Cada raíz de 3 letras o más pasa a búsqueda por prefijo ('tall' -> 'tall':*). El stemmer
# Snowball español reduce "taller" a `tall` pero "talleres" a `taller`: sin prefijo, el singular
# no encuentra el plural. Además encuentra palabras a medio escribir ("intelig").
_RAIZ = r"'((?:[^']|''){3,})'"
_RAIZ_PREFIJO = r"'\1':*"


def _consulta_ts(texto: str):
    """websearch_to_tsquery nunca falla por sintaxis: admite "frase", or y -palabra.

    Su resultado ya está normalizado (sin tildes, con raíces); se reinterpreta con 'simple'
    para no volver a aplicar el stemmer sobre las raíces.
    """
    web = func.websearch_to_tsquery(literal_column("'public.es_unaccent'::regconfig"), texto)
    return func.to_tsquery(
        literal_column("'simple'::regconfig"),
        func.regexp_replace(cast(web, Text), _RAIZ, _RAIZ_PREFIJO, "g"),
    )


def _documento():
    # Exactamente la expresión de ix_publicacion_busqueda, para que el planificador use el índice.
    return func.publicacion_documento(Publicacion.titulo, Publicacion.contenido)


def _inicio_del_dia(dia) -> ColumnElement:
    # 00:00 de ese día en Caracas, como instante: Postgres trae los datos de zona horaria.
    return func.timezone(ZONA, cast(cast(dia, Date), DateTime))


def _condiciones_muro(
    filtros: FiltrosMuro, etiqueta: str | None, rol_id: int | None, yo: int | None
) -> list[ColumnElement]:
    """El mismo WHERE para la página y para el conteo (modelo-de-datos.md §7)."""
    condiciones = [
        Publicacion.estado_id == EstadoPublicacionId.VISIBLE,
        _visible_para(rol_id, yo),
    ]
    if filtros.texto:
        condiciones.append(_documento().op("@@")(_consulta_ts(filtros.texto)))
    if filtros.categoria_id:
        condiciones.append(Publicacion.categoria_id == filtros.categoria_id)
    if filtros.autor_id:
        condiciones.append(Publicacion.autor_id == filtros.autor_id)
    if etiqueta:
        condiciones.append(
            select(PublicacionEtiqueta.publicacion_id)
            .join(Etiqueta, Etiqueta.id == PublicacionEtiqueta.etiqueta_id)
            .where(
                PublicacionEtiqueta.publicacion_id == Publicacion.id, Etiqueta.nombre == etiqueta
            )
            .exists()
        )
    if filtros.desde:
        condiciones.append(Publicacion.publicada_en >= _inicio_del_dia(filtros.desde))
    if filtros.hasta:
        # "Hasta el 30" incluye todo el 30: < 1 de octubre 00:00 en Caracas.
        condiciones.append(
            Publicacion.publicada_en < _inicio_del_dia(filtros.hasta + timedelta(days=1))
        )
    return condiciones


def muro(
    db: Session,
    filtros: FiltrosMuro,
    etiqueta: str | None,
    rol_id: int | None,
    yo: int | None,
    cursor: Cursor | None,
    limite: int,
) -> list[Publicacion]:
    """Siempre por fecha (F3.1, F3.4), paginado por cursor (D15). Trae limite + 1 filas."""
    stmt = select(Publicacion).where(*_condiciones_muro(filtros, etiqueta, rol_id, yo))
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Publicacion.publicada_en, Publicacion.id) < (cursor.fecha, cursor.id)
        )
    stmt = stmt.order_by(Publicacion.publicada_en.desc(), Publicacion.id.desc()).limit(limite + 1)
    return list(db.scalars(stmt))


def contar_muro(
    db: Session, filtros: FiltrosMuro, etiqueta: str | None, rol_id: int | None, yo: int | None
) -> int:
    """Conteo con tope: CONTEO_MAX + 1 significa "más de CONTEO_MAX" (F3.3)."""
    sub = (
        select(literal_column("1"))
        .select_from(Publicacion)
        .where(*_condiciones_muro(filtros, etiqueta, rol_id, yo))
        .limit(CONTEO_MAX + 1)
        .subquery()
    )
    return db.scalar(select(func.count()).select_from(sub)) or 0


def consulta_vacia(db: Session, texto: str) -> bool:
    """True si la búsqueda solo tiene palabras vacías ("de la"): no encontraría nada."""
    return db.scalar(select(func.numnode(_consulta_ts(texto)) == 0)) or False


def fragmentos(db: Session, ids: list[int], texto: str) -> dict[int, str]:
    """ts_headline solo para las filas de la página: es caro (modelo-de-datos.md §7).

    Las coincidencias quedan entre MARCA_INICIO y MARCA_FIN. Antes se quitan esas marcas del
    contenido, así un autor no puede fabricar un resaltado.
    """
    if not ids:
        return {}
    opciones = (
        f"StartSel={MARCA_INICIO}, StopSel={MARCA_FIN}, MaxFragments=2, MaxWords=30, "
        'MinWords=12, FragmentDelimiter=" … "'
    )
    limpio = func.translate(Publicacion.contenido, MARCA_INICIO + MARCA_FIN, "")
    headline = func.ts_headline(
        literal_column("'public.es_unaccent'::regconfig"), limpio, _consulta_ts(texto), opciones
    )
    filas = db.execute(select(Publicacion.id, headline).where(Publicacion.id.in_(ids))).all()
    return {fila[0]: fila[1] for fila in filas}


def autores_con_visibles(
    db: Session, candidatos: list[int], rol_id: int | None, yo: int | None, limite: int
) -> list[int]:
    """De los candidatos, los que tienen alguna publicación visible para quien consulta."""
    if not candidatos:
        return []
    visibles = set(
        db.scalars(
            select(Publicacion.autor_id)
            .where(
                Publicacion.autor_id.in_(candidatos),
                Publicacion.estado_id == EstadoPublicacionId.VISIBLE,
                _visible_para(rol_id, yo),
            )
            .distinct()
        )
    )
    return [c for c in candidatos if c in visibles][:limite]


# ---------------------------------------------------------------- bandeja de moderación (M4)
def _condiciones_bandeja(filtros: FiltrosBandeja) -> list[ColumnElement]:
    """Sin audiencia ni estado de visibilidad: el administrador ve todo (F4.1, F3.2)."""
    condiciones: list[ColumnElement] = []
    if filtros.estado == FiltroModeracion.SIN_REVISAR:
        condiciones.append(Publicacion.revisada.is_(False))  # ix_publicacion_sin_revisar
    elif filtros.estado == FiltroModeracion.REVISADA:
        condiciones += [
            Publicacion.revisada.is_(True),
            Publicacion.estado_id != EstadoPublicacionId.OCULTA_MODERACION,
        ]
    elif filtros.estado == FiltroModeracion.OCULTA:
        condiciones.append(Publicacion.estado_id == EstadoPublicacionId.OCULTA_MODERACION)
    if filtros.categoria_id:
        condiciones.append(Publicacion.categoria_id == filtros.categoria_id)
    if filtros.autor_id:
        condiciones.append(Publicacion.autor_id == filtros.autor_id)
    if filtros.desde:
        condiciones.append(Publicacion.publicada_en >= _inicio_del_dia(filtros.desde))
    if filtros.hasta:
        condiciones.append(
            Publicacion.publicada_en < _inicio_del_dia(filtros.hasta + timedelta(days=1))
        )
    return condiciones


def bandeja(
    db: Session, filtros: FiltrosBandeja, cursor: Cursor | None, limite: int
) -> list[Publicacion]:
    """Las más recientes primero (F4.1, HU-19), paginado por cursor. Trae limite + 1 filas."""
    stmt = select(Publicacion).where(*_condiciones_bandeja(filtros))
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Publicacion.publicada_en, Publicacion.id) < (cursor.fecha, cursor.id)
        )
    stmt = stmt.order_by(Publicacion.publicada_en.desc(), Publicacion.id.desc()).limit(limite + 1)
    return list(db.scalars(stmt))


def _contar(db: Session, condicion: ColumnElement) -> int:
    """Con tope, como el muro: CONTEO_MAX + 1 significa "más de CONTEO_MAX"."""
    sub = (
        select(literal_column("1"))
        .select_from(Publicacion)
        .where(condicion)
        .limit(CONTEO_MAX + 1)
        .subquery()
    )
    return db.scalar(select(func.count()).select_from(sub)) or 0


def contar_sin_revisar(db: Session) -> int:
    return _contar(db, Publicacion.revisada.is_(False))


def contar_ocultas_moderacion(db: Session) -> int:
    return _contar(db, Publicacion.estado_id == EstadoPublicacionId.OCULTA_MODERACION)


def ocultamientos(db: Session, ids: list[int]) -> dict[int, datetime]:
    """Fecha del ocultamiento vigente de varias publicaciones (v_publicacion_moderacion)."""
    if not ids:
        return {}
    filas = db.execute(
        text(
            "SELECT publicacion_id, ocultada_en FROM v_publicacion_moderacion "
            "WHERE publicacion_id = ANY(:ids)"
        ),
        {"ids": ids},
    ).all()
    return {fila.publicacion_id: fila.ocultada_en for fila in filas}


# ---------------------------------------------------------------- categorías: administración (M5)
def get_categoria_para_modificar(db: Session, categoria_id: int) -> Categoria | None:
    return db.scalars(
        select(Categoria).where(Categoria.id == categoria_id).with_for_update()
    ).one_or_none()


def publicaciones_por_categoria(db: Session) -> dict[int, int]:
    filas = db.execute(
        select(Publicacion.categoria_id, func.count()).group_by(Publicacion.categoria_id)
    ).all()
    return {categoria_id: total for categoria_id, total in filas}


def insertar_categoria(db: Session, categoria: Categoria) -> Categoria:
    db.add(categoria)
    db.flush()
    db.refresh(categoria)
    return categoria
