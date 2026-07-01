import uuid as _uuid
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings

Base = declarative_base()
engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except:  # noqa: E722
        db.rollback()
        raise
    finally:
        db.close()


def _migrate_uuid(eng) -> None:
    """Idempotent startup migration: ensure uuid column exists and backfill nulls."""
    insp = inspect(eng)
    if not insp.has_table("files"):
        return
    cols = [c["name"] for c in insp.get_columns("files")]
    with eng.begin() as conn:
        if "uuid" not in cols:
            conn.execute(text("ALTER TABLE files ADD COLUMN uuid VARCHAR(36)"))
        rows = conn.execute(
            text("SELECT id FROM files WHERE uuid IS NULL OR uuid = ''")
        ).fetchall()
        for (rid,) in rows:
            conn.execute(
                text("UPDATE files SET uuid = :u WHERE id = :i"),
                {"u": str(_uuid.uuid4()), "i": rid},
            )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_uuid(engine)
