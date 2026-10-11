from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.core.errors import PROBLEM_JSON, ConflictError


def _add_test_routes(app: FastAPI) -> None:
    @app.get("/_test/conflict")
    def conflict() -> None:
        raise ConflictError("El correo ya está registrado")

    @app.get("/_test/validate")
    def validate(page: int = Query(ge=1)) -> dict[str, int]:
        return {"page": page}

    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError("fallo inesperado")


def test_app_error_uses_problem_json(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    res = client.get("/_test/conflict")
    assert res.status_code == 409
    assert res.headers["content-type"] == PROBLEM_JSON
    body = res.json()
    assert body["code"] == "conflict"
    assert body["detail"] == "El correo ya está registrado"
    assert body["request_id"] == res.headers["X-Request-ID"]


def test_validation_error_lists_fields(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    res = client.get("/_test/validate", params={"page": 0})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["field"] == "page"


def test_not_found_route(client: TestClient) -> None:
    res = client.get("/api/v1/no-existe")
    assert res.status_code == 404
    assert res.json()["code"] == "not_found"


def test_unhandled_error_hides_details(app: FastAPI, client: TestClient) -> None:
    _add_test_routes(app)
    res = client.get("/_test/boom")
    assert res.status_code == 500
    body = res.json()
    assert body["code"] == "internal_error"
    assert "fallo inesperado" not in res.text
