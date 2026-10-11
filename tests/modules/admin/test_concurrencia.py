"""Dos administradores a la vez: siempre queda uno activo y cada solicitud se resuelve una vez."""

import threading

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_sessionmaker
from app.modules.identity import service
from app.modules.identity.exceptions import UltimoAdministrador
from app.modules.identity.schemas import RolId, UsuarioActual
from tests.helpers import sql
from tests.modules.admin.conftest import ADMIN, id_de, pedir_profesor, solicitud_pendiente


def test_el_trigger_de_ultimo_admin_se_traduce_a_409(usuario) -> None:
    """Petición con autenticación ya vieja: B pasó el control de rol, pero entretanto A la
    desactivó. Lo que queda es dejar sin administradores: la base lo impide."""
    usuario("administrador", correo="a@facyt.uc.edu.ve")
    usuario("administrador", correo="b@facyt.uc.edu.ve")
    a, b = id_de("a@facyt.uc.edu.ve"), id_de("b@facyt.uc.edu.ve")
    sql("UPDATE usuario SET rol_id = 1 WHERE id = :b", b=b)  # B ya no es admin
    b_viejo = UsuarioActual(
        id=b, rol_id=RolId.ADMINISTRADOR, sesion_id="x", debe_cambiar_password=False
    )
    with get_sessionmaker()() as db, pytest.raises(UltimoAdministrador):
        service.cambiar_rol(db, b_viejo, a, RolId.ESTUDIANTE, None)
    assert sql("SELECT rol_id, activo FROM usuario WHERE id = :a", a=a) == [(3, True)]
    assert sql("SELECT count(*) FROM evento_cuenta")[0][0] == 0


def test_dos_admins_se_quitan_el_rol_a_la_vez(client: TestClient, usuario) -> None:
    a = usuario("administrador", correo="a@facyt.uc.edu.ve")
    b = usuario("administrador", correo="b@facyt.uc.edu.ve")
    ids = {"a": id_de("a@facyt.uc.edu.ve"), "b": id_de("b@facyt.uc.edu.ve")}
    barrera = threading.Barrier(2)
    codigos: list[int] = []

    def quitar(headers: dict[str, str], objetivo: int) -> None:
        barrera.wait()
        res = client.put(f"{ADMIN}/usuarios/{objetivo}/rol", headers=headers, json={"rol_id": 1})
        codigos.append(res.status_code)

    hilos = [
        threading.Thread(target=quitar, args=(a, ids["b"])),
        threading.Thread(target=quitar, args=(b, ids["a"])),
    ]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    # Uno gana; el otro recibe 409 (trigger) o 403 (ya no es admin al autenticarse).
    assert sorted(codigos)[0] == 200
    assert sorted(codigos)[1] in (403, 409)
    assert sql("SELECT count(*) FROM usuario WHERE rol_id = 3 AND activo")[0][0] == 1


def test_dos_aprobaciones_de_la_misma_solicitud(client: TestClient, usuario) -> None:
    a = usuario("administrador", correo="a@facyt.uc.edu.ve")
    b = usuario("administrador", correo="b@facyt.uc.edu.ve")
    pedir_profesor(client, usuario())
    sid = solicitud_pendiente("estudiante@facyt.uc.edu.ve")
    barrera = threading.Barrier(2)
    codigos: list[int] = []

    def aprobar(headers: dict[str, str]) -> None:
        barrera.wait()
        res = client.post(f"{ADMIN}/solicitudes-profesor/{sid}/aprobar", headers=headers)
        codigos.append(res.status_code)

    hilos = [threading.Thread(target=aprobar, args=(h,)) for h in (a, b)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(codigos) == [200, 409]
    assert sql("SELECT count(*) FROM evento_cuenta")[0][0] == 1
