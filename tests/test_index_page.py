from fastapi.testclient import TestClient
from app.main import create_app

client = TestClient(create_app())


def test_index_served_without_auth():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "File Storage" in resp.text


def test_index_references_api():
    resp = client.get("/")
    # страница должна обращаться к /files через fetch
    assert "/files" in resp.text


def test_index_has_edit_controls():
    resp = client.get("/")
    # редактирование метаданных (PATCH) и замена файла (PUT content)
    assert "PATCH" in resp.text
    assert "/content" in resp.text
    assert "btn-edit" in resp.text
    assert "btn-replace" in resp.text
