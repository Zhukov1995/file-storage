import pytest
from app.categories import validate


def test_unknown_category():
    with pytest.raises(ValueError):
        validate("nope", "x.ifc", 10)


def test_bad_extension():
    with pytest.raises(ValueError):
        validate("image", "model.ifc", 10)


def test_oversize():
    with pytest.raises(ValueError):
        validate("svg", "icon.svg", 6 * 1024 * 1024)


def test_ok():
    validate("bim", "model.ifc", 1000)  # no raise
