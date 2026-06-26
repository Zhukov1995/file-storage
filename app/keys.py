import re
from typing import Optional
from uuid import uuid4

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_segment(seg: str) -> str:
    """Sanitize a single path segment using the same rule as filenames."""
    return _SAFE.sub("_", seg).strip("_") or "segment"


def normalize_folder(folder: Optional[str]) -> str:
    if not folder or folder == "/":
        return "/"
    parts = []
    for p in folder.split("/"):
        if not p or p in (".", ".."):
            # skip empty, current-dir, and parent-dir traversal segments
            continue
        parts.append(_safe_segment(p))
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_") or "file"


def build_object_key(category: str, folder: str, filename: str) -> str:
    folder = normalize_folder(folder)
    prefix = category if folder == "/" else f"{category}{folder}"
    return f"{prefix}/{uuid4().hex}_{_safe_name(filename)}"
