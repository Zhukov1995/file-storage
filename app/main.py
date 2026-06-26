from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from app.auth import require_api_key
from app.routes import router
from app.db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="Universal File Storage", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/_protected", dependencies=[Depends(require_api_key)])
    def _protected() -> dict:
        return {"ok": True}

    _INDEX = Path(__file__).parent / "static" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX.read_text(encoding="utf-8")

    @app.on_event("startup")
    def _startup() -> None:
        try:
            init_db()
        except Exception:
            pass  # DB may be unavailable in unit tests; routes are overridden there

    app.include_router(router)
    return app


app = create_app()
