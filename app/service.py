from typing import List, Optional
from sqlalchemy import select
from app.models import FileRecord
from app.categories import validate
from app.keys import build_object_key, normalize_folder


class FileService:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def create(self, name, category, folder, tags, metadata, filename,
               fileobj, content_type) -> FileRecord:
        # Determine size via seek — works for SpooledTemporaryFile and BytesIO
        fileobj.seek(0, 2)
        size_hint = fileobj.tell()
        fileobj.seek(0)
        # Validate category, extension AND size BEFORE touching the store
        validate(category, filename, size_hint)
        key = build_object_key(category, folder, filename)
        # Stream directly — do not buffer into a second BytesIO
        size = self.store.put_stream(key, fileobj, content_type)
        rec = FileRecord(
            name=name, category=category, folder=normalize_folder(folder),
            tags=tags or [], object_key=key, file_name=filename,
            file_size=size, content_type=content_type, metadata_=metadata or {},
        )
        try:
            self.db.add(rec)
            self.db.commit()
            self.db.refresh(rec)
        except Exception:
            self.db.rollback()
            self.store.delete(key)  # compensation: no orphan object
            raise
        return rec

    def list(self, category=None, folder=None, prefix=None, tag=None,
             q=None, limit=50, offset=0) -> List[FileRecord]:
        stmt = select(FileRecord)
        if category:
            stmt = stmt.where(FileRecord.category == category)
        if folder:
            stmt = stmt.where(FileRecord.folder == normalize_folder(folder))
        if prefix:
            stmt = stmt.where(FileRecord.folder.like(normalize_folder(prefix) + "%"))
        if q:
            stmt = stmt.where(FileRecord.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(FileRecord.created_at.desc()).limit(limit).offset(offset)
        rows = list(self.db.execute(stmt).scalars().all())
        if tag:
            rows = [r for r in rows if tag in (r.tags or [])]
        return rows

    def get(self, id: int) -> FileRecord:
        rec = self.db.get(FileRecord, id)
        if rec is None:
            raise KeyError(id)
        return rec

    def update(self, id: int, **fields) -> FileRecord:
        rec = self.get(id)
        for f in ("name", "category", "folder", "tags"):
            if f in fields and fields[f] is not None:
                setattr(rec, f, normalize_folder(fields[f]) if f == "folder" else fields[f])
        if fields.get("metadata") is not None:
            rec.metadata_ = fields["metadata"]
        # Re-validate if category was changed: current filename must be allowed
        if "category" in fields and fields["category"] is not None:
            validate(rec.category, rec.file_name, rec.file_size or 0)
        self.db.commit()
        self.db.refresh(rec)
        return rec

    def replace_content(self, id, filename, fileobj, content_type) -> FileRecord:
        rec = self.get(id)
        # Determine size via seek before touching the store
        fileobj.seek(0, 2)
        size_hint = fileobj.tell()
        fileobj.seek(0)
        # Validate BEFORE uploading
        validate(rec.category, filename, size_hint)
        old_key = rec.object_key
        new_key = build_object_key(rec.category, rec.folder, filename)
        # Stream directly without buffering into a second BytesIO
        size = self.store.put_stream(new_key, fileobj, content_type)
        rec.object_key = new_key
        rec.file_name = filename
        rec.file_size = size
        rec.content_type = content_type
        try:
            self.db.commit()
            self.db.refresh(rec)
        except Exception:
            self.db.rollback()
            self.store.delete(new_key)  # compensation: remove orphan new object
            raise
        # Only delete old object after successful commit
        self.store.delete(old_key)
        return rec

    def delete(self, id: int) -> None:
        rec = self.get(id)
        key = rec.object_key
        # Delete DB row first (commit), then idempotently remove the object.
        # This order avoids a dangling DB reference to a deleted object if the
        # store.delete call succeeds but a subsequent step rolls back.
        self.db.delete(rec)
        self.db.commit()
        self.store.delete(key)

    def list_folders(self, category: Optional[str] = None) -> List[str]:
        stmt = select(FileRecord.folder).distinct()
        if category:
            stmt = stmt.where(FileRecord.category == category)
        return sorted(r[0] for r in self.db.execute(stmt).all())
