import json
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Response
from botocore.exceptions import ClientError, BotoCoreError
from app.auth import require_api_key
from app.db import get_db
from app.storage import ObjectStore
from app.service import FileService
from app.schemas import FileOut, FilePatch

router = APIRouter(dependencies=[Depends(require_api_key)])

_STORE_ERRORS = (ClientError, BotoCoreError)


def get_service(db=Depends(get_db)) -> FileService:
    return FileService(db, ObjectStore())


def _parse_tags(tags: Optional[str]) -> list:
    if not tags:
        return []
    return [t.strip() for t in tags.split(",") if t.strip()]


@router.post("/files", response_model=FileOut, status_code=201)
def upload(
    name: str = Form(...),
    category: str = Form(...),
    folder: str = Form("/"),
    tags: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    file: UploadFile = File(...),
    svc: FileService = Depends(get_service),
):
    try:
        meta = json.loads(metadata) if metadata else {}
        rec = svc.create(name, category, folder, _parse_tags(tags), meta,
                         file.filename, file.file, file.content_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return FileOut(**rec.to_dict())


@router.get("/files", response_model=List[FileOut])
def list_files(category: Optional[str] = None, folder: Optional[str] = None,
               prefix: Optional[str] = None, tag: Optional[str] = None,
               q: Optional[str] = None, limit: int = 50, offset: int = 0,
               svc: FileService = Depends(get_service)):
    rows = svc.list(category, folder, prefix, tag, q, limit, offset)
    return [FileOut(**r.to_dict()) for r in rows]


@router.get("/files/{file_id}", response_model=FileOut)
def get_file(file_id: int, svc: FileService = Depends(get_service)):
    try:
        rec = svc.get(file_id)
        out = rec.to_dict()
        out["download_url"] = svc.store.presigned_get(rec.object_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return FileOut(**out)


@router.patch("/files/{file_id}", response_model=FileOut)
def patch_file(file_id: int, body: FilePatch, svc: FileService = Depends(get_service)):
    try:
        rec = svc.update(file_id, **body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return FileOut(**rec.to_dict())


@router.put("/files/{file_id}/content", response_model=FileOut)
def replace_content(file_id: int, file: UploadFile = File(...),
                    svc: FileService = Depends(get_service)):
    try:
        rec = svc.replace_content(file_id, file.filename, file.file, file.content_type)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return FileOut(**rec.to_dict())


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: int, svc: FileService = Depends(get_service)):
    try:
        svc.delete(file_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return Response(status_code=204)


@router.get("/folders", response_model=List[str])
def list_folders(category: Optional[str] = None,
                 svc: FileService = Depends(get_service)):
    return svc.list_folders(category)
