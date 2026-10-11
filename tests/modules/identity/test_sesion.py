"""F1.4, F1.5, F1.6 · HU-03, HU-04."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.modules.identity.dependencies import require_role
from app.modules.identity.schemas import RolId
from tests.helpers import PASSWORD, sql

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/usuarios/me"
COOKIE = get_settings().refresh_cookie_name


def _cookie(client: TestClient) -> str | None:
    return client.cookies.get(COOKIE, path="/api/v1/auth")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- login
def test_login_devuelve_token_y_cookie_segura(client: TestClient, registrar) -> None:
    registrar()
    res = client.post(LOGIN, json={"correo": "ANA@facyt.uc.edu.ve", "password": PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["debe_cambiar_password"] is False
    set_cookie = res.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in set_cookie
    for flag in ("httponly", "secure", "samesite=lax", "path=/api/v1/auth"):
        assert flag in set_cookie
    # Solo se guarda el SHA-256 del refresh token, nunca el token (D12).
    [(guardado,)] = sql("SELECT token_hash FROM refresh_token")
    assert len(guardado) == 32
    assert _cookie(client).encode() != bytes(guardado)


def test_errores_de_login_son_genericos(client: TestClient, registrar) -> None:
    usuario = registrar()
    mala = client.post(LOGIN, json={"correo": "ana@facyt.uc.edu.ve", "password": "otra-clave-1"})
    inexistente = client.post(LOGIN, json={"correo": "nadie@facyt.uc.edu.ve", "password": PASSWORD})
    sql("UPDATE usuario SET activo = false WHERE id = :id", id=usuario["id"])
    inactiva = client.post(LOGIN, json={"correo": "ana@facyt.uc.edu.ve", "password": PASSWORD})
    for res in (mala, inexistente, inactiva):
        assert res.status_code == 401
        assert res.json()["code"] == "invalid_credentials"
        assert res.json()["title"] == mala.json()["title"]


# ---------------------------------------------------------------- F1.5
def test_sin_token_401_con_www_authenticate(client: TestClient) -> None:
    res = client.get(ME)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert client.get(ME, headers=_bearer("basura")).status_code == 401


def test_require_role_y_cambio_de_rol_rige_en_la_siguiente_peticion(
    app: FastAPI, client: TestClient, registrar, login
) -> None:
    @app.get("/_test/admin", dependencies=[Depends(require_role(RolId.ADMINISTRADOR))])
    def solo_admin() -> dict[str, bool]:
        return {"ok": True}

    usuario = registrar()
    headers = login()
    res = client.get("/_test/admin", headers=headers)
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"

    # Mismo access token: el rol se revalida contra la base en cada petición.
    sql("UPDATE usuario SET rol_id = 3 WHERE id = :id", id=usuario["id"])
    assert client.get("/_test/admin", headers=headers).status_code == 200


def test_cuenta_desactivada_pierde_el_acceso_al_instante(
    client: TestClient, registrar, login
) -> None:
    usuario = registrar()
    headers = login()
    sql("UPDATE usuario SET activo = false WHERE id = :id", id=usuario["id"])
    assert client.get(ME, headers=headers).status_code == 401
    assert client.post(REFRESH).status_code == 401


# ---------------------------------------------------------------- refresh
def test_refresh_rota_el_token(client: TestClient, registrar, login) -> None:
    registrar()
    login()
    viejo = _cookie(client)
    res = client.post(REFRESH)
    assert res.status_code == 200
    nuevo = _cookie(client)
    assert nuevo and nuevo != viejo
    assert client.get(ME, headers=_bearer(res.json()["access_token"])).status_code == 200
    assert sql("SELECT count(*) FROM refresh_token WHERE usado_en IS NULL") == [(1,)]


def test_reuso_de_refresh_revoca_la_sesion(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    robado = _cookie(client)
    assert client.post(REFRESH).status_code == 200  # el usuario legítimo renueva

    client.cookies.set(COOKIE, robado, path="/api/v1/auth")  # el atacante usa el viejo
    assert client.post(REFRESH).status_code == 401
    assert sql("SELECT motivo_revocacion FROM sesion") == [("reuso",)]
    # Cae la sesión entera, incluido el access token vigente.
    assert client.get(ME, headers=headers).status_code == 401


def test_refresh_vencido_por_inactividad(client: TestClient, registrar, login) -> None:
    registrar()
    login()
    sql(
        "UPDATE refresh_token SET emitido_en = now() - interval '9 hours', "
        "expira_en = now() - interval '1 hour'"
    )
    res = client.post(REFRESH)
    assert res.status_code == 401
    assert "max-age=0" in res.headers["set-cookie"].lower()  # la cookie inútil se borra


def test_refresh_respeta_el_tope_absoluto(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    sql(
        "UPDATE sesion SET creada_en = now() - interval '8 days', "
        "expira_absoluta_en = now() - interval '1 day'"
    )
    assert client.post(REFRESH).status_code == 401
    assert client.get(ME, headers=headers).status_code == 401


def test_refresh_no_extiende_el_tope(client: TestClient, registrar, login) -> None:
    registrar()
    login()
    sql("UPDATE sesion SET expira_absoluta_en = now() + interval '1 hour'")
    assert client.post(REFRESH).status_code == 200
    [(dentro_del_tope,)] = sql(
        "SELECT rt.expira_en <= s.expira_absoluta_en FROM refresh_token rt "
        "JOIN sesion s ON s.id = rt.sesion_id WHERE rt.usado_en IS NULL"
    )
    assert dentro_del_tope


def test_refresh_sin_cookie(client: TestClient) -> None:
    assert client.post(REFRESH).status_code == 401


# ---------------------------------------------------------------- logout
def test_logout_invalida_el_access_copiado_y_el_refresh(
    client: TestClient, registrar, login
) -> None:
    registrar()
    headers = login()
    copiado = _cookie(client)
    res = client.post(LOGOUT, headers=headers)
    assert res.status_code == 204
    assert "max-age=0" in res.headers["set-cookie"].lower()
    assert client.get(ME, headers=headers).status_code == 401  # F1.6: aunque lo hayan copiado
    client.cookies.set(COOKIE, copiado, path="/api/v1/auth")
    assert client.post(REFRESH).status_code == 401
    assert sql("SELECT motivo_revocacion FROM sesion") == [("logout",)]


def test_logout_solo_con_cookie(client: TestClient, registrar, login) -> None:
    registrar()
    headers = login()
    assert client.post(LOGOUT).status_code == 204
    assert client.get(ME, headers=headers).status_code == 401


def test_logout_no_afecta_otras_sesiones(client: TestClient, registrar, login) -> None:
    registrar()
    otra = login()
    actual = login()
    client.post(LOGOUT, headers=actual)
    assert client.get(ME, headers=otra).status_code == 200
    assert client.get(ME, headers=actual).status_code == 401
