from typing import List, Optional, Any, Dict
from pydantic import BaseModel


class FileOut(BaseModel):
    id: int
    name: str
    category: str
    folder: str
    tags: List[str]
    object_key: str
    file_name: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    metadata: Dict[str, Any] = {}
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    download_url: Optional[str] = None


class FilePatch(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    folder: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
