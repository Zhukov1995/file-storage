import io


def test_put_exists_presign_delete(store, sample):
    key = "image/test/_unit.png"
    n = store.put_stream(key, sample, "image/png")
    assert n == len(b"hello-ifc-bytes")
    assert store.exists(key) is True
    url = store.presigned_get(key)
    assert key in url
    store.delete(key)
    assert store.exists(key) is False


def test_delete_is_idempotent(store):
    store.delete("image/does/not/_exist.png")  # no raise
