from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import FileRecord


def make_session():
    eng = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_create_and_to_dict():
    s = make_session()
    rec = FileRecord(
        name="Building A",
        category="bim",
        folder="/estate-1",
        tags=["estate-1", "corpus-a"],
        object_key="bim/estate-1/uuid_model.ifc",
        file_name="model.ifc",
        file_size=1234,
        content_type="application/x-ifc",
        metadata_={"foo": "bar"},
    )
    s.add(rec)
    s.commit()
    d = rec.to_dict()
    assert d["name"] == "Building A"
    assert d["category"] == "bim"
    assert d["folder"] == "/estate-1"
    assert d["tags"] == ["estate-1", "corpus-a"]
    assert d["metadata"] == {"foo": "bar"}
    assert d["status"] == "ready"
    assert isinstance(d["created_at"], str)
