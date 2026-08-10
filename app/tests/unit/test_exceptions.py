from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import AppError, register_exception_handlers


def test_app_error_uses_standard_shape() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise AppError(
            status_code=409,
            code="conflict",
            message="The resource conflicts",
            details={"field": "title"},
        )

    response = TestClient(app).get("/failure")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "The resource conflicts",
            "details": {"field": "title"},
        }
    }


def test_http_404_uses_standard_shape() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    response = TestClient(app).get("/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"
