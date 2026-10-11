"""F1.1, F1.2, F1.3 · HU-01, HU-02."""

import pytest
from fastapi.testclient import TestClient

from tests.helpers import PASSWORD, sql

URL = "/api/v1/auth/registro"


def test_registro_crea_estudiante_sin_exponer_el_hash(client: TestClient, registrar) -> None:
    usuario = registrar()
    assert usuario["rol"]["codigo"] == "estudiante"
    assert usuario["correo"] == "ana@facyt.uc.edu.ve"
    assert "password" not in str(usuario) and "hash" not in str(usuario)
    [(hash_,)] = sql("SELECT password_hash FROM usuario WHERE id = :id", id=usuario["id"])
    assert hash_.startswith("$argon2") and PASSWORD not in hash_  # F1.3
    assert sql("SELECT count(*) FROM solicitud_profesor") == [(0,)]


def test_correo_duplicado_sin_distinguir_mayusculas(client: TestClient, registrar) -> None:
    registrar()
    res = client.post(
        URL,
        json={"nombre_completo": "Otra", "correo": "ANA@facyt.uc.edu.ve", "password": PASSWORD},
    )
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "email_taken"
    assert body["errors"][0]["field"] == "correo"


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("password", "corta1"),  # menos de 8
        ("password", "solo-letras"),  # sin dígito
        ("password", "12345678"),  # sin letra
        ("password", "a1" * 65),  # más de 128
        ("correo", "no-es-correo"),
        ("nombre_completo", " "),
    ],
)
def test_validaciones_por_campo(client: TestClient, campo: str, valor: str) -> None:
    body = {"nombre_completo": "Ana Pérez", "correo": "ana@facyt.uc.edu.ve", "password": PASSWORD}
    res = client.post(URL, json={**body, campo: valor})
    assert res.status_code == 422
    assert res.json()["errors"][0]["field"] == campo


def test_password_igual_al_correo(client: TestClient) -> None:
    correo = "ana1@facyt.uc.edu.ve"
    res = client.post(URL, json={"nombre_completo": "Ana", "correo": correo, "password": correo})
    assert res.status_code == 422


def test_soy_profesor_crea_solicitud_pendiente_y_sigue_estudiante(
    client: TestClient, registrar, login
) -> None:
    usuario = registrar(soy_profesor=True)
    assert usuario["rol"]["codigo"] == "estudiante"
    perfil = client.get("/api/v1/usuarios/me", headers=login()).json()
    assert perfil["solicitud_profesor"]["estado"] == "pendiente"


def test_segunda_solicitud_pendiente_no_se_permite(client: TestClient, registrar, login) -> None:
    registrar(soy_profesor=True)
    res = client.post("/api/v1/usuarios/me/solicitud-profesor", headers=login())
    assert res.status_code == 409
    assert res.json()["code"] == "request_pending"


def test_solicitud_desde_el_perfil_y_de_nuevo_tras_rechazo(
    client: TestClient, registrar, login
) -> None:
    usuario = registrar()
    headers = login()
    res = client.post("/api/v1/usuarios/me/solicitud-profesor", headers=headers)
    assert res.status_code == 201
    assert res.json()["estado"] == "pendiente"

    # Un administrador la rechaza (Módulo 5; aquí se simula en la base).
    [(admin_id,)] = sql(
        "INSERT INTO usuario (nombre_completo, correo, password_hash, rol_id) "
        "VALUES ('Admin', 'admin@facyt.uc.edu.ve', '$argon2id$x', 3) RETURNING id"
    )
    sql(
        "UPDATE solicitud_profesor SET estado_id = 3, resuelta_por = :a, resuelta_en = now(), "
        "motivo_rechazo = 'No se pudo verificar' WHERE usuario_id = :u",
        a=admin_id,
        u=usuario["id"],
    )
    perfil = client.get("/api/v1/usuarios/me", headers=headers).json()
    assert perfil["solicitud_profesor"]["estado"] == "rechazada"
    assert perfil["solicitud_profesor"]["motivo_rechazo"] == "No se pudo verificar"

    res = client.post("/api/v1/usuarios/me/solicitud-profesor", headers=headers)
    assert res.status_code == 201


def test_profesor_no_puede_solicitar(client: TestClient, registrar, login) -> None:
    usuario = registrar()
    sql("UPDATE usuario SET rol_id = 2 WHERE id = :id", id=usuario["id"])
    res = client.post("/api/v1/usuarios/me/solicitud-profesor", headers=login())
    assert res.status_code == 409
    assert res.json()["code"] == "already_professor"
