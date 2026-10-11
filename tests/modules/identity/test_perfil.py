"""F1.7, F5.10 · HU-05, HU-26."""

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.modules.identity import service
from tests.helpers import PASSWORD, sql
from tests.helpers import imagen as _imagen

ME = "/api/v1/usuarios/me"
FOTO = "/api/v1/usuarios/me/foto"
MEDIA = Path(get_settings().storage_local_dir)


def _cambiar_password(client: TestClient, headers: dict, actual: str, nueva: str):
    return client.put(
        f"{ME}/password",
        headers=headers,
        json={"password_actual": actual, "password_nueva": nueva},
    )


def test_perfil_muestra_datos_y_editar_nombre(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    perfil = client.get(ME, headers=headers).json()
    assert perfil["nombre_completo"] == "Ana Pérez"
    assert perfil["rol"]["codigo"] == "estudiante"
    assert perfil["foto_url"] is None
    assert perfil["solicitud_profesor"] is None

    res = client.patch(ME, headers=headers, json={"nombre_completo": "  Ana María Pérez "})
    assert res.status_code == 200
    assert res.json()["nombre_completo"] == "Ana María Pérez"


def test_correo_y_rol_no_se_editan(client: TestClient, registrar, login) -> None:
    registrar()
    res = client.patch(
        ME,
        headers=login(),
        json={"nombre_completo": "Ana", "correo": "otro@facyt.uc.edu.ve", "rol_id": 3},
    )
    assert res.status_code == 200
    assert res.json()["correo"] == "ana@facyt.uc.edu.ve"
    assert res.json()["rol"]["codigo"] == "estudiante"


def test_cambio_de_password_exige_la_actual(client: TestClient, registrar, login) -> None:
    registrar()
    res = _cambiar_password(client, login(), "equivocada1", "nueva-clave-456")
    assert res.status_code == 400
    assert res.json()["errors"][0]["field"] == "password_actual"
    login()  # la contraseña anterior sigue sirviendo


def test_cambio_de_password_cierra_las_demas_sesiones(client: TestClient, registrar, login) -> None:
    registrar()
    otra = login()
    actual = login()
    assert _cambiar_password(client, actual, PASSWORD, "nueva-clave-456").status_code == 204
    assert client.get(ME, headers=actual).status_code == 200
    assert client.get(ME, headers=otra).status_code == 401
    login(password="nueva-clave-456")


def test_password_nueva_igual_al_correo(client: TestClient, registrar, login) -> None:
    registrar(correo="ana2026@facyt.uc.edu.ve")
    headers = login("ana2026@facyt.uc.edu.ve")
    res = _cambiar_password(client, headers, PASSWORD, "ANA2026@facyt.uc.edu.ve")
    assert res.status_code == 422


def test_admin_inicial_debe_cambiar_la_password(client: TestClient) -> None:
    with Session(get_engine()) as db:
        service.crear_admin_inicial(
            db, nombre="Administrador inicial", correo="admin@facyt.uc.edu.ve", password=PASSWORD
        )
    res = client.post(
        "/api/v1/auth/login", json={"correo": "admin@facyt.uc.edu.ve", "password": PASSWORD}
    )
    assert res.json()["debe_cambiar_password"] is True
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    assert client.get(ME, headers=headers).status_code == 200
    bloqueado = client.patch(ME, headers=headers, json={"nombre_completo": "Otro"})
    assert bloqueado.status_code == 403
    assert bloqueado.json()["code"] == "password_change_required"

    assert _cambiar_password(client, headers, PASSWORD, "definitiva-789").status_code == 204
    assert client.patch(ME, headers=headers, json={"nombre_completo": "Otro"}).status_code == 200


def test_foto_se_recodifica_y_la_anterior_se_borra(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    res = client.put(FOTO, headers=headers, files={"foto": ("a.png", _imagen(), "image/png")})
    assert res.status_code == 200, res.text
    [(primera,)] = sql("SELECT foto_ruta FROM usuario")
    assert primera.endswith(".webp")
    assert res.json()["foto_url"].endswith(primera)
    with Image.open(MEDIA / primera) as img:
        assert img.format == "WEBP"
        assert img.size == (512, 512)

    jpg = _imagen("JPEG")
    res = client.put(FOTO, headers=headers, files={"foto": ("b.jpg", jpg, "image/jpeg")})
    assert res.status_code == 200
    assert not (MEDIA / primera).exists()  # la cola archivo_por_borrar se vació tras el commit
    assert sql("SELECT count(*) FROM archivo_por_borrar") == [(0,)]

    [(segunda,)] = sql("SELECT foto_ruta FROM usuario")
    res = client.delete(FOTO, headers=headers)
    assert res.status_code == 200
    assert res.json()["foto_url"] is None
    assert not (MEDIA / segunda).exists()


def test_foto_rechaza_lo_que_no_es_imagen(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    falso = client.put(
        FOTO, headers=headers, files={"foto": ("x.png", b"<?php echo 1; ?>", "image/png")}
    )
    assert falso.status_code == 422
    assert falso.json()["code"] == "invalid_image"

    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    res = client.put(FOTO, headers=headers, files={"foto": ("x.gif", gif.getvalue(), "image/gif")})
    assert res.status_code == 422
    assert sql("SELECT foto_ruta FROM usuario") == [(None,)]
