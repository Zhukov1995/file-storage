from fastapi.testclient import TestClient
from app.main import create_app
from app.config import get_settings

client = TestClient(create_app())


def test_protected_requires_key():
    resp = client.get("/_protected")
    assert resp.status_code == 401


def test_protected_rejects_wrong_key():
    resp = client.get("/_protected", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_protected_accepts_correct_key():
    key = get_settings().api_key
    resp = client.get("/_protected", headers={"X-API-Key": key})
    assert resp.status_code == 200
