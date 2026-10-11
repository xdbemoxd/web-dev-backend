"""Casos de uso y reglas de negocio del módulo posts. Es su API pública.

Cada función que escribe hace commit al terminar; el repository nunca lo hace. La excepción es
`moderar`, que confirma el M4 junto con su historial.
Las imágenes se validan y re-codifican todas antes de subir la primera, y si la transacción
falla se borran las que ya se subieron: nunca queda un archivo sin fila ni una fila sin archivo.
"""

import logging
import uuid

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import constraint_violada
from app.core.imagenes import TIPO_MIME, ImagenInvalida, procesar_imagen
from app.core.pagination import CursorPage, CursorParams, encode_cursor
from app.core.storage import Storage, get_storage
from app.modules.identity import service as identity
from app.modules.identity.schemas import UsuarioActual
from app.modules.posts import repository as repo
from app.modules.posts.exceptions import (
    AccesoRestringido,
    CategoriaNoEncontrada,
    DatosInvalidos,
    ModeracionDesactualizada,
    ModeracionNoPermitida,
    NombreCategoriaRepetido,
    OcultaPorModeracion,
    PublicacionNoEncontrada,
    SoloElAutor,
)
from app.modules.posts.models import (
    Categoria,
    ImagenPublicacion,
    Publicacion,
    PublicacionEtiqueta,
)
from app.modules.posts.schemas import (
    CONTEO_MAX,
    ETIQUETA_MAX,
    EXTRACTO_MAX,
    MAX_ETIQUETAS,
    MAX_IMAGENES,
    AccionModeracionId,
    AutorSugeridoOut,
    CatalogoOut,
    CategoriaAdminOut,
    CategoriaIn,
    CategoriaOut,
    CategoriaRefOut,
    EstadoModeracionOut,
    EstadoPublicacionId,
    FilaBandejaOut,
    FiltrosBandeja,
    FiltrosMuro,
    ImagenOut,
    InstantaneaModeracion,
    ModeracionOut,
    MuroPage,
    PublicacionIn,
    PublicacionOut,
    PublicacionResumenOut,
    SegmentoOut,
    TarjetaDatos,
    TarjetaOut,
)

logger = logging.getLogger(__name__)

IMAGEN_LADO_MAX = 2048  # px del lado mayor; la portada no se recorta (la tarjeta del M6 la usa)
NUEVA = "nueva:"  # prefijo de `orden_imagenes` para los archivos que llegan en la edición


# ---------------------------------------------------------------- validación (F2.6)
def _validar_categoria(db: Session, categoria_id: int) -> None:
    # El trigger tg_publicacion_categoria_activa también lo impide, pero sin decir qué campo.
    if not repo.categoria_activa(db, categoria_id):
        raise DatosInvalidos(
            "categoria_id", "La categoría no existe o ya no está disponible", "invalid_category"
        )


def _normalizar_etiquetas(db: Session, etiquetas: list[str]) -> list[str]:
    """F2.5: "Jornadas 2026" y "jornadas2026" son la misma. El límite cuenta tras normalizar."""
    nombres: list[str] = []
    for i, nombre in enumerate(repo.normalizar_etiquetas(db, etiquetas)):
        if not nombre:
            raise DatosInvalidos(f"etiquetas.{i}", "La etiqueta debe tener letras o números")
        if len(nombre) > ETIQUETA_MAX:
            raise DatosInvalidos(
                f"etiquetas.{i}", f"La etiqueta no puede pasar de {ETIQUETA_MAX} caracteres"
            )
        if nombre not in nombres:
            nombres.append(nombre)
    if len(nombres) > MAX_ETIQUETAS:
        raise DatosInvalidos("etiquetas", f"Máximo {MAX_ETIQUETAS} etiquetas por publicación")
    return nombres


def _procesar_imagenes(archivos: list[bytes]) -> list[bytes]:
    webps = []
    for i, data in enumerate(archivos):
        try:
            webps.append(procesar_imagen(data, lado_max=IMAGEN_LADO_MAX))
        except ImagenInvalida as exc:
            raise ImagenInvalida(
                exc.detail,
                errors=[{"field": f"imagenes.{i}", "message": exc.detail, "type": "invalid_image"}],
            ) from exc
    return webps


def _parsear_orden(orden_imagenes: list[str], existentes: set[int], nuevas: int) -> list[int | str]:
    """`orden_imagenes` = ["12", "nueva:0", "15"]: ids que se conservan y archivos nuevos.

    Devuelve la lista con ints (ids existentes) y "nueva:N" validados.
    """
    resultado: list[int | str] = []
    for i, token in enumerate(orden_imagenes):
        campo = f"orden_imagenes.{i}"
        if token.startswith(NUEVA):
            indice = token.removeprefix(NUEVA)
            if not indice.isdigit() or int(indice) >= nuevas:
                raise DatosInvalidos(campo, f"No se envió el archivo {token}")
            valor: int | str = f"{NUEVA}{int(indice)}"
        elif token.isdigit() and int(token) in existentes:
            valor = int(token)
        else:
            raise DatosInvalidos(campo, "La imagen no pertenece a esta publicación")
        if valor in resultado:
            raise DatosInvalidos(campo, "La imagen está repetida")
        resultado.append(valor)
    usados = {v for v in resultado if isinstance(v, str)}
    if len(usados) != nuevas:
        raise DatosInvalidos("orden_imagenes", "Cada imagen nueva debe aparecer en el orden")
    if len(resultado) > MAX_IMAGENES:
        raise DatosInvalidos("imagenes", f"Máximo {MAX_IMAGENES} imágenes por publicación")
    return resultado


# ---------------------------------------------------------------- escritura
def _asignar_etiquetas(db: Session, pub: Publicacion, nombres: list[str]) -> None:
    """Conserva las filas de las etiquetas que siguen (solo cambia su posición) y borra el resto."""
    por_nombre = repo.asegurar_etiquetas(db, nombres)
    actuales = {pe.etiqueta_id: pe for pe in pub.etiquetas}
    nuevas = []
    for posicion, nombre in enumerate(nombres, start=1):
        etiqueta = por_nombre[nombre]
        pe = actuales.pop(etiqueta.id, None) or PublicacionEtiqueta(etiqueta=etiqueta)
        pe.posicion = posicion
        nuevas.append(pe)
    pub.etiquetas = nuevas


def _subir(storage: Storage, pub_id: int, webp: bytes, subidas: list[str]) -> ImagenPublicacion:
    # Nombre asignado por el sistema; el original del archivo no se guarda (F2.4).
    ruta = f"posts/{pub_id}/{uuid.uuid4().hex}.webp"
    storage.put(ruta, webp, TIPO_MIME)
    subidas.append(ruta)
    return ImagenPublicacion(
        ruta_almacenamiento=ruta, tipo_mime=TIPO_MIME, tamano_bytes=len(webp), orden=0
    )


def _deshacer_subidas(storage: Storage, subidas: list[str]) -> None:
    if not subidas:
        return
    try:
        storage.delete(subidas)
    except Exception:
        logger.exception("no se pudieron borrar %d imágenes huérfanas", len(subidas))


def crear(
    db: Session,
    actual: UsuarioActual,
    datos: PublicacionIn,
    archivos: list[bytes],
    storage: Storage | None = None,
) -> PublicacionOut:
    """F2.1-F2.6. Nace visible y sin revisar; autor y fecha los pone el sistema."""
    storage = storage or get_storage()
    if len(archivos) > MAX_IMAGENES:
        raise DatosInvalidos("imagenes", f"Máximo {MAX_IMAGENES} imágenes por publicación")
    _validar_categoria(db, datos.categoria_id)
    etiquetas = _normalizar_etiquetas(db, datos.etiquetas)
    webps = _procesar_imagenes(archivos)

    pub = Publicacion(
        autor_id=actual.id,
        categoria_id=datos.categoria_id,
        audiencia_id=datos.audiencia_id,
        titulo=datos.titulo,
        contenido=datos.contenido,
    )
    subidas: list[str] = []
    try:
        db.add(pub)
        db.flush()  # id para la ruta de las imágenes
        _asignar_etiquetas(db, pub, etiquetas)
        for orden, webp in enumerate(webps, start=1):
            imagen = _subir(storage, pub.id, webp, subidas)
            imagen.orden = orden
            pub.imagenes.append(imagen)
        db.commit()
    except Exception:
        db.rollback()
        _deshacer_subidas(storage, subidas)
        raise
    db.refresh(pub)  # relaciones ya cargadas (categoría, estado) con los valores nuevos
    return detalle(db, pub.id, actual, storage)


def _propia(db: Session, actual: UsuarioActual, publicacion_id: int) -> Publicacion:
    pub = repo.get_para_modificar(db, publicacion_id)
    if pub is None:
        raise PublicacionNoEncontrada()
    if pub.autor_id != actual.id:
        raise SoloElAutor()
    return pub


def editar(
    db: Session,
    actual: UsuarioActual,
    publicacion_id: int,
    datos: PublicacionIn,
    orden_imagenes: list[str],
    archivos: list[bytes],
    storage: Storage | None = None,
) -> tuple[PublicacionOut, bool]:
    """F2.7. Reemplaza todos los campos (mismo formulario que crear).

    `orden_imagenes` es el orden final: ids de imágenes que se conservan y "nueva:N" para el
    archivo N de `archivos`. Las que no aparecen se borran (el trigger las encola).
    Devuelve la publicación y si quedaron archivos por borrar del almacenamiento.
    """
    storage = storage or get_storage()
    pub = _propia(db, actual, publicacion_id)
    if datos.categoria_id != pub.categoria_id:
        # Si se mantiene una categoría que luego se desactivó, la publicación la conserva (F5.9).
        _validar_categoria(db, datos.categoria_id)
    etiquetas = _normalizar_etiquetas(db, datos.etiquetas)
    existentes = {img.id: img for img in pub.imagenes}
    orden = _parsear_orden(orden_imagenes, set(existentes), len(archivos))
    webps = _procesar_imagenes(archivos)

    subidas: list[str] = []
    try:
        repo.diferir_orden(db)
        pub.titulo = datos.titulo
        pub.contenido = datos.contenido
        pub.categoria_id = datos.categoria_id
        pub.audiencia_id = datos.audiencia_id
        pub.editada_en = func.now()  # hora de la transacción; publicada_en no cambia
        if pub.estado_id != EstadoPublicacionId.OCULTA_MODERACION:
            # Vuelve a "Sin revisar" (F4.2). Oculta por moderación sigue oculta y revisada.
            pub.revisada = False
        _asignar_etiquetas(db, pub, etiquetas)
        imagenes = []
        for posicion, valor in enumerate(orden, start=1):
            if isinstance(valor, int):
                imagen = existentes.pop(valor)
            else:
                imagen = _subir(storage, pub.id, webps[int(valor.removeprefix(NUEVA))], subidas)
            imagen.orden = posicion
            imagenes.append(imagen)
        pub.imagenes = imagenes  # las que quedaron en `existentes` se borran (delete-orphan)
        db.commit()
    except Exception:
        db.rollback()
        _deshacer_subidas(storage, subidas)
        raise
    db.refresh(pub)  # relaciones ya cargadas (categoría, estado) con los valores nuevos
    return detalle(db, pub.id, actual, storage), bool(existentes)


def cambiar_visibilidad(
    db: Session, actual: UsuarioActual, publicacion_id: int, visible: bool
) -> PublicacionOut:
    """F2.8. Visible <-> Oculta por el autor. Lo que ocultó un administrador no se toca."""
    pub = _propia(db, actual, publicacion_id)
    if pub.estado_id == EstadoPublicacionId.OCULTA_MODERACION:
        motivo, ocultada_en = repo.motivo_moderacion(db, pub.id) or (None, None)
        db.rollback()
        raise OcultaPorModeracion(motivo, ocultada_en)
    pub.estado_id = EstadoPublicacionId.VISIBLE if visible else EstadoPublicacionId.OCULTA_AUTOR
    db.commit()
    db.refresh(pub)
    return detalle(db, pub.id, actual)


def eliminar(db: Session, actual: UsuarioActual, publicacion_id: int) -> None:
    """F2.9. Definitiva. El CASCADE borra imágenes y etiquetas asignadas y el trigger encola
    los archivos; el historial de moderación se conserva con publicacion_id NULL (F4.6)."""
    _propia(db, actual, publicacion_id)
    db.execute(delete(Publicacion).where(Publicacion.id == publicacion_id))
    db.commit()
    db.expunge_all()


# ---------------------------------------------------------------- lectura
def _salida(
    db: Session, pub: Publicacion, actual: UsuarioActual | None, storage: Storage
) -> PublicacionOut:
    es_autor = actual is not None and actual.id == pub.autor_id
    moderacion = None
    if pub.estado_id == EstadoPublicacionId.OCULTA_MODERACION and (
        es_autor or (actual is not None and actual.es_admin)
    ):
        fila = repo.motivo_moderacion(db, pub.id)
        if fila:
            moderacion = ModeracionOut(motivo=fila[0], ocultada_en=fila[1])
    return PublicacionOut(
        id=pub.id,
        titulo=pub.titulo,
        contenido=pub.contenido,
        categoria=CategoriaRefOut.model_validate(pub.categoria),
        audiencia=CatalogoOut.model_validate(pub.audiencia),
        estado=CatalogoOut.model_validate(pub.estado),
        autor=identity.autores(db, [pub.autor_id], storage)[pub.autor_id],
        imagenes=[
            ImagenOut(id=img.id, url=storage.public_url(img.ruta_almacenamiento), orden=img.orden)
            for img in pub.imagenes
        ],
        etiquetas=[pe.etiqueta.nombre for pe in pub.etiquetas],
        publicada_en=pub.publicada_en,
        editada_en=pub.editada_en,
        es_autor=es_autor,
        moderacion=moderacion,
    )


def detalle(
    db: Session,
    publicacion_id: int,
    actual: UsuarioActual | None,
    storage: Storage | None = None,
) -> PublicacionOut:
    """F3.6 (mínimo del M3). Distingue "no existe" (404) de "no puedes verla" (403)."""
    storage = storage or get_storage()
    pub = repo.detalle_visible(
        db,
        publicacion_id,
        rol_id=actual.rol_id if actual else None,
        yo=actual.id if actual else None,
        es_admin=actual is not None and actual.es_admin,
    )
    if pub is None:
        if not repo.existe(db, publicacion_id):
            raise PublicacionNoEncontrada()
        raise AccesoRestringido("Inicia sesión para verla." if actual is None else None)
    return _salida(db, pub, actual, storage)


def mis_publicaciones(
    db: Session, actual: UsuarioActual, params: CursorParams, storage: Storage | None = None
) -> CursorPage[PublicacionResumenOut]:
    """F2.8: todas las del autor, también las ocultas, con su estado."""
    storage = storage or get_storage()
    filas = repo.mis_publicaciones(db, actual.id, params.cursor, params.limite)
    items = [
        PublicacionResumenOut(
            id=pub.id,
            titulo=pub.titulo,
            categoria=CategoriaRefOut.model_validate(pub.categoria),
            audiencia=CatalogoOut.model_validate(pub.audiencia),
            estado=CatalogoOut.model_validate(pub.estado),
            portada_url=(
                storage.public_url(pub.imagenes[0].ruta_almacenamiento) if pub.imagenes else None
            ),
            etiquetas=[pe.etiqueta.nombre for pe in pub.etiquetas],
            publicada_en=pub.publicada_en,
            editada_en=pub.editada_en,
        )
        for pub in filas
    ]
    return CursorPage[PublicacionResumenOut].build(
        items, params, lambda item: (item.publicada_en, item.id)
    )


def categorias(db: Session, incluir_inactivas: bool = False) -> list[CategoriaOut]:
    """F2.2: el formulario ofrece solo las activas. F3.3/F5.9: el filtro del muro, todas."""
    return [CategoriaOut.model_validate(c) for c in repo.categorias(db, incluir_inactivas)]


def sugerir_etiquetas(db: Session, prefijo: str, actual: UsuarioActual) -> list[str]:
    """F2.5: autocompletado con etiquetas existentes que quien escribe puede ver (D16)."""
    normalizado = repo.normalizar_etiquetas(db, [prefijo])[0]
    if not normalizado:
        return []
    return repo.sugerir_etiquetas(db, normalizado, actual.rol_id, actual.id)


# ---------------------------------------------------------------- muro y búsqueda (M3)
def _extracto(contenido: str) -> list[SegmentoOut]:
    """Inicio del contenido para la tarjeta (F3.1), cortado en un límite de palabra."""
    texto = " ".join(contenido.split())
    if len(texto) > EXTRACTO_MAX:
        texto = texto[:EXTRACTO_MAX].rsplit(" ", 1)[0] + " …"
    return [SegmentoOut(texto=texto)]


def _segmentar(fragmento: str) -> list[SegmentoOut]:
    """Convierte la salida de ts_headline en segmentos. Nunca se devuelve HTML (ts_headline no
    es seguro para insertarlo en una página)."""
    segmentos: list[SegmentoOut] = []
    for i, parte in enumerate(fragmento.split(repo.MARCA_INICIO)):
        if i == 0:
            resaltado, resto = "", parte
        else:
            resaltado, _, resto = parte.partition(repo.MARCA_FIN)
        if resaltado:
            segmentos.append(SegmentoOut(texto=resaltado, resaltado=True))
        if resto:
            segmentos.append(SegmentoOut(texto=resto))
    return segmentos


def muro(
    db: Session,
    actual: UsuarioActual | None,
    filtros: FiltrosMuro,
    params: CursorParams,
    storage: Storage | None = None,
) -> MuroPage:
    """F3.1-F3.5 y F3.7. Solo lo visible para quien consulta (F3.2), siempre por fecha."""
    storage = storage or get_storage()
    rol_id = actual.rol_id if actual else None
    yo = actual.id if actual else None

    etiqueta = None
    if filtros.etiqueta:
        etiqueta = repo.normalizar_etiquetas(db, [filtros.etiqueta])[0]
        if not etiqueta:
            raise DatosInvalidos("etiqueta", "La etiqueta debe tener letras o números")
    if filtros.texto and repo.consulta_vacia(db, filtros.texto):
        raise DatosInvalidos(
            "texto", "La búsqueda solo tiene palabras muy comunes; escribe palabras más específicas"
        )

    filas = repo.muro(db, filtros, etiqueta, rol_id, yo, params.cursor, params.limite)
    pagina = filas[: params.limite]
    autores = identity.autores(db, [pub.autor_id for pub in pagina], storage)
    fragmentos = (
        repo.fragmentos(db, [pub.id for pub in pagina], filtros.texto) if filtros.texto else {}
    )
    items = [
        TarjetaOut(
            id=pub.id,
            titulo=pub.titulo,
            extracto=(
                _segmentar(fragmentos[pub.id]) if pub.id in fragmentos else _extracto(pub.contenido)
            ),
            portada_url=(
                storage.public_url(pub.imagenes[0].ruta_almacenamiento) if pub.imagenes else None
            ),
            categoria=CategoriaRefOut.model_validate(pub.categoria),
            audiencia=CatalogoOut.model_validate(pub.audiencia),
            autor=autores[pub.autor_id],
            etiquetas=[pe.etiqueta.nombre for pe in pub.etiquetas],
            publicada_en=pub.publicada_en,
            editada_en=pub.editada_en,
        )
        for pub in pagina
    ]
    siguiente = None
    if len(filas) > params.limite:
        siguiente = encode_cursor(pagina[-1].publicada_en, pagina[-1].id)
    pagina_out = MuroPage(items=items, siguiente=siguiente)
    if params.cursor is None:
        # El número de resultados solo en la primera página: no cambia al paginar (F3.3).
        total = repo.contar_muro(db, filtros, etiqueta, rol_id, yo)
        pagina_out.total = min(total, CONTEO_MAX)
        pagina_out.total_supera = total > CONTEO_MAX
    return pagina_out


def sugerir_autores(
    db: Session, texto: str, actual: UsuarioActual | None, limite: int = 10
) -> list[AutorSugeridoOut]:
    """F3.3: autores por nombre, solo si tienen publicaciones visibles para quien busca."""
    candidatos = identity.buscar_por_nombre(db, texto)
    ids = repo.autores_con_visibles(
        db, candidatos, actual.rol_id if actual else None, actual.id if actual else None, limite
    )
    datos = identity.autores(db, ids)
    return [AutorSugeridoOut(id=i, nombre_completo=datos[i].nombre_completo) for i in ids]


# ---------------------------------------------------------------- moderación (API para M4)
# Qué acción aplica a qué estado (F4.2-F4.5). Eliminar aplica a cualquiera.
_ORIGENES = {
    AccionModeracionId.REVISAR: {EstadoPublicacionId.VISIBLE, EstadoPublicacionId.OCULTA_AUTOR},
    AccionModeracionId.OCULTAR: {EstadoPublicacionId.VISIBLE, EstadoPublicacionId.OCULTA_AUTOR},
    AccionModeracionId.RESTAURAR: {EstadoPublicacionId.OCULTA_MODERACION},
}


def _marca(pub: Publicacion) -> str:
    """Huella del estado que el administrador tiene en pantalla. Cambia si otro administrador
    actúa o si el autor edita u oculta la publicación."""
    editada = pub.editada_en.isoformat() if pub.editada_en else "-"
    return f"{pub.estado_id}.{int(pub.revisada)}.{editada}"


def bandeja(
    db: Session, filtros: FiltrosBandeja, params: CursorParams, storage: Storage | None = None
) -> CursorPage[FilaBandejaOut]:
    """F4.1. Todas las publicaciones, de cualquier audiencia, de la más reciente a la más
    antigua. La autorización (solo administradores) la hace quien llama."""
    storage = storage or get_storage()
    filas = repo.bandeja(db, filtros, params.cursor, params.limite)
    autores = identity.autores(db, [pub.autor_id for pub in filas], storage)
    ocultas = repo.ocultamientos(
        db, [p.id for p in filas if p.estado_id == EstadoPublicacionId.OCULTA_MODERACION]
    )
    items = [
        FilaBandejaOut(
            id=pub.id,
            titulo=pub.titulo,
            extracto=_extracto(pub.contenido),
            portada_url=(
                storage.public_url(pub.imagenes[0].ruta_almacenamiento) if pub.imagenes else None
            ),
            categoria=CategoriaRefOut.model_validate(pub.categoria),
            audiencia=CatalogoOut.model_validate(pub.audiencia),
            estado=CatalogoOut.model_validate(pub.estado),
            autor=autores[pub.autor_id],
            revisada=pub.revisada,
            publicada_en=pub.publicada_en,
            editada_en=pub.editada_en,
            ocultada_en=ocultas.get(pub.id),
            editada_tras_ocultar=bool(
                pub.id in ocultas and pub.editada_en and pub.editada_en > ocultas[pub.id]
            ),
            marca=_marca(pub),
        )
        for pub in filas
    ]
    return CursorPage[FilaBandejaOut].build(items, params, lambda f: (f.publicada_en, f.id))


def contar_sin_revisar(db: Session) -> int:
    return min(repo.contar_sin_revisar(db), CONTEO_MAX)


def contar_ocultas(db: Session) -> int:
    return min(repo.contar_ocultas_moderacion(db), CONTEO_MAX)


def estado_moderacion(db: Session, publicacion_id: int) -> EstadoModeracionOut:
    pub = repo.get_publicacion(db, publicacion_id)
    if pub is None:
        raise PublicacionNoEncontrada()
    return EstadoModeracionOut(revisada=pub.revisada, marca=_marca(pub))


def moderar(
    db: Session, publicacion_id: int, accion: AccionModeracionId, marca: str
) -> InstantaneaModeracion:
    """F4.2-F4.5. Aplica la acción del administrador **sin commit**: el M4 la confirma junto
    con la fila del historial, en la misma transacción (modelo-de-datos.md §5, punto 2).

    La fila queda bloqueada hasta ese commit, así dos administradores no actúan a la vez.
    `marca` es la que el administrador vio (`FilaBandejaOut.marca`); si ya no coincide, algo
    cambió desde entonces y no se aplica nada.
    """
    pub = repo.get_para_modificar(db, publicacion_id)
    if pub is None:
        raise PublicacionNoEncontrada()
    if marca != _marca(pub):
        raise ModeracionDesactualizada()
    instantanea = InstantaneaModeracion(
        publicacion_id=pub.id, titulo=pub.titulo, autor_id=pub.autor_id
    )

    if accion == AccionModeracionId.ELIMINAR:
        # El CASCADE borra imágenes y etiquetas; el trigger encola los archivos (D14).
        db.execute(delete(Publicacion).where(Publicacion.id == pub.id))
        db.expunge(pub)
        instantanea.eliminada = True
        return instantanea

    if pub.estado_id not in _ORIGENES[accion] or (
        accion == AccionModeracionId.REVISAR and pub.revisada
    ):
        raise ModeracionNoPermitida()
    if accion == AccionModeracionId.OCULTAR:
        pub.estado_id = EstadoPublicacionId.OCULTA_MODERACION
    elif accion == AccionModeracionId.RESTAURAR:
        # Vuelve al muro en su fecha original (F4.4): publicada_en no cambia.
        pub.estado_id = EstadoPublicacionId.VISIBLE
    pub.revisada = True  # junto con el estado: ck_publicacion_moderacion
    db.flush()
    return instantanea


# ---------------------------------------------------------------- categorías: administración (M5)
COLOR_POR_DEFECTO = "#1E40AF"  # el mismo DEFAULT de categoria.color en la base


# Las categorías no se borran: la FK de publicacion es RESTRICT y el histórico las necesita
# (F5.9). Desactivar las saca del formulario, no del filtro del muro.
def categorias_admin(db: Session) -> list[CategoriaAdminOut]:
    """F5.7-F5.9: todas, con su estado y cuántas publicaciones las usan."""
    usos = repo.publicaciones_por_categoria(db)
    return [
        CategoriaAdminOut(
            id=c.id,
            nombre=c.nombre,
            descripcion=c.descripcion,
            color=c.color,
            activa=c.activa,
            creada_en=c.creada_en,
            publicaciones=usos.get(c.id, 0),
        )
        for c in repo.categorias(db, incluir_inactivas=True)
    ]


def _categoria_admin(db: Session, categoria_id: int) -> CategoriaAdminOut:
    return next(c for c in categorias_admin(db) if c.id == categoria_id)


def _guardar_categoria(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if constraint_violada(exc) == "uq_categoria_nombre_ci":
            raise NombreCategoriaRepetido() from exc
        raise


def crear_categoria(db: Session, datos: CategoriaIn) -> CategoriaAdminOut:
    """F5.7: nace activa y aparece de inmediato en el formulario y en los filtros (HU-25)."""
    try:
        categoria = repo.insertar_categoria(
            db,
            Categoria(
                nombre=datos.nombre,
                descripcion=datos.descripcion or "",
                color=datos.color or COLOR_POR_DEFECTO,
            ),
        )
    except IntegrityError as exc:
        db.rollback()
        if constraint_violada(exc) == "uq_categoria_nombre_ci":
            raise NombreCategoriaRepetido() from exc
        raise
    _guardar_categoria(db)
    return _categoria_admin(db, categoria.id)


def editar_categoria(db: Session, categoria_id: int, datos: CategoriaIn) -> CategoriaAdminOut:
    """F5.8: las publicaciones la conservan con el nombre nuevo (se une por id)."""
    categoria = repo.get_categoria_para_modificar(db, categoria_id)
    if categoria is None:
        db.rollback()
        raise CategoriaNoEncontrada()
    categoria.nombre = datos.nombre
    if datos.descripcion is not None:
        categoria.descripcion = datos.descripcion
    if datos.color is not None:
        categoria.color = datos.color
    _guardar_categoria(db)
    return _categoria_admin(db, categoria_id)


def cambiar_estado_categoria(db: Session, categoria_id: int, activa: bool) -> CategoriaAdminOut:
    """F5.9: desactivar o reactivar. Idempotente: repetir el mismo estado no es un error."""
    categoria = repo.get_categoria_para_modificar(db, categoria_id)
    if categoria is None:
        db.rollback()
        raise CategoriaNoEncontrada()
    categoria.activa = activa
    db.commit()
    return _categoria_admin(db, categoria_id)


# -------------------------------------------------------- tarjeta para Instagram (API para M6)
def para_tarjeta(
    db: Session, actual: UsuarioActual, publicacion_id: int, storage: Storage | None = None
) -> TarjetaDatos:
    """Datos para la tarjeta del M6 (F6.2). Aplica la misma visibilidad que el detalle (404 o
    403); quién puede pedirla y la regla "solo públicas" las decide el M6.

    Expone la ruta de la portada en storage para que el M6 la lea sin interpretar URLs.
    """
    storage = storage or get_storage()
    salida = detalle(db, publicacion_id, actual, storage)
    pub = repo.get_publicacion(db, publicacion_id)
    if pub is None:  # la eliminaron entre las dos lecturas
        raise PublicacionNoEncontrada()
    return TarjetaDatos(
        publicacion=salida,
        portada_ruta=pub.imagenes[0].ruta_almacenamiento if pub.imagenes else None,
        version=pub.editada_en or pub.publicada_en,
    )
