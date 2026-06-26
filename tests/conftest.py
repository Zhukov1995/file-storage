import io
import os
import pytest
from app.storage import ObjectStore


def _minio_up() -> bool:
    import socket
    from urllib.parse import urlparse
    ep = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    host = urlparse(ep).hostname
    port = urlparse(ep).port or 9000
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def store():
    if not _minio_up():
        pytest.skip("MinIO not reachable")
    s = ObjectStore()
    s.ensure_bucket()
    return s


@pytest.fixture
def sample():
    return io.BytesIO(b"hello-ifc-bytes")
