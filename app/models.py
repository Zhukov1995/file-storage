from datetime import datetime
from typing import List
from sqlalchemy import String, BigInteger, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, Text
import json
import uuid as uuid_module
from app.db import Base


class TagList(TypeDecorator):
    """Portable string-list: native ARRAY on PG would be ideal, but JSON keeps
    the model SQLite-testable. Stored as JSON text."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value or [])

    def process_result_value(self, value, dialect):
        return json.loads(value) if value else []


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, index=True,
        default=lambda: str(uuid_module.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64), index=True)
    folder: Mapped[str] = mapped_column(String(512), default="/", index=True)
    tags: Mapped[List[str]] = mapped_column(TagList, default=list)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str] = mapped_column(String(127), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uuid": self.uuid,
            "name": self.name,
            "category": self.category,
            "folder": self.folder,
            "tags": self.tags,
            "object_key": self.object_key,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "content_type": self.content_type,
            "metadata": self.metadata_,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
