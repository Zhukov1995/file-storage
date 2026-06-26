from app.keys import normalize_folder, build_object_key


def test_normalize_root():
    assert normalize_folder(None) == "/"
    assert normalize_folder("") == "/"
    assert normalize_folder("/") == "/"


def test_normalize_strips_trailing():
    assert normalize_folder("/a/b/") == "/a/b"
    assert normalize_folder("a/b") == "/a/b"


def test_build_key_root():
    key = build_object_key("bim", "/", "model.ifc")
    assert key.startswith("bim/")
    assert key.endswith("_model.ifc")
    assert "//" not in key


def test_build_key_nested():
    key = build_object_key("bim", "/estate-1", "model.ifc")
    assert key.startswith("bim/estate-1/")
    assert "//" not in key


# C1: path traversal tests
def test_normalize_folder_strips_dotdot():
    result = normalize_folder("/a/../../b")
    assert ".." not in result
    # After stripping .. segments only 'a' and 'b' survive; the .. segments are dropped
    assert result == "/a/b"


def test_normalize_folder_strips_dot():
    assert normalize_folder("/a/./b") == "/a/b"


def test_normalize_folder_all_dotdot_becomes_root():
    result = normalize_folder("/../..")
    assert result == "/"


def test_build_key_no_traversal():
    key = build_object_key("bim", "/a/../x", "f.ifc")
    assert ".." not in key
