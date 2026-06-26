import io
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from botocore.exceptions import BotoCoreError
from app.main import create_app
from app.db import Base, get_db
from app.routes import get_service
from app.service import FileService
from app.config import get_settings
from tests.test_service import FakeStore

eng = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(eng)
TestingSession = sessionmaker(bind=eng)
_store = FakeStore()

app = create_app()


def _svc_override():
    db = TestingSession()
    try:
        yield FileService(db, _store)
    finally:
        db.close()


app.dependency_overrides[get_service] = _svc_override
client = TestClient(app)
KEY = {"X-API-Key": get_settings().api_key}


def test_upload_requires_key():
    resp = client.post("/files", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")},
                       data={"name": "A", "category": "svg"})
    assert resp.status_code == 401


def test_upload_then_list_get_delete():
    resp = client.post(
        "/files", headers=KEY,
        files={"file": ("a.svg", b"<svg/>", "image/svg+xml")},
        data={"name": "Icon", "category": "svg", "folder": "/icons", "tags": "ui,small"},
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    assert resp.json()["folder"] == "/icons"
    assert resp.json()["tags"] == ["ui", "small"]

    lst = client.get("/files", headers=KEY, params={"category": "svg"})
    assert lst.status_code == 200 and len(lst.json()) == 1

    one = client.get(f"/files/{fid}", headers=KEY)
    assert one.status_code == 200 and "download_url" in one.json()

    dele = client.delete(f"/files/{fid}", headers=KEY)
    assert dele.status_code == 204
    assert client.get(f"/files/{fid}", headers=KEY).status_code == 404


def test_upload_bad_category_422():
    resp = client.post(
        "/files", headers=KEY,
        files={"file": ("a.ifc", b"x", "application/x-ifc")},
        data={"name": "A", "category": "nope"},
    )
    assert resp.status_code == 422


# I6: botocore errors map to 502
def test_upload_store_error_returns_502():
    """When the object store raises BotoCoreError during upload, route returns 502."""
    class BrokenStore(FakeStore):
        def put_stream(self, key, fileobj, ct):
            raise BotoCoreError()

    def _broken_svc():
        db = TestingSession()
        try:
            yield FileService(db, BrokenStore())
        finally:
            db.close()

    broken_app = create_app()
    broken_app.dependency_overrides[get_service] = _broken_svc
    c = TestClient(broken_app, raise_server_exceptions=False)
    resp = c.post(
        "/files", headers=KEY,
        files={"file": ("i.svg", b"<svg/>", "image/svg+xml")},
        data={"name": "X", "category": "svg"},
    )
    assert resp.status_code == 502
