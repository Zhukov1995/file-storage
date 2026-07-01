import json
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import StreamingResponse
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


@router.get("/files/{file_ref}", response_model=FileOut)
def get_file(file_ref: str, svc: FileService = Depends(get_service)):
    try:
        rec = svc.get_by_ref(file_ref)
        out = rec.to_dict()
        out["download_url"] = svc.store.presigned_get(rec.object_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return FileOut(**out)


@router.get(
    "/files/{file_ref}/content",
    summary="Stream raw file content",
    description=(
        "Streams the raw bytes of the stored object with its original "
        "`Content-Type` and an `inline` `Content-Disposition`, so clients "
        "(e.g. an in-app preview or a server-side proxy) can render or "
        "download the file without a presigned MinIO URL. "
        "`file_ref` accepts either the integer `id` or the UUID string."
    ),
    responses={
        200: {
            "description": "Raw file bytes streamed with the original content type.",
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"description": "File not found"},
        502: {"description": "Object storage error"},
    },
)
def get_content(file_ref: str, svc: FileService = Depends(get_service)):
    try:
        rec = svc.get_by_ref(file_ref)
        body = svc.store.get_stream(rec.object_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return StreamingResponse(
        body,
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{rec.file_name}"'},
    )


@router.patch("/files/{file_ref}", response_model=FileOut)
def patch_file(file_ref: str, body: FilePatch, svc: FileService = Depends(get_service)):
    try:
        rec = svc.get_by_ref(file_ref)
        rec = svc.update(rec.id, **body.model_dump(exclude_unset=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return FileOut(**rec.to_dict())


@router.put("/files/{file_ref}/content", response_model=FileOut)
def replace_content(file_ref: str, file: UploadFile = File(...),
                    svc: FileService = Depends(get_service)):
    try:
        rec = svc.get_by_ref(file_ref)
        rec = svc.replace_content(rec.id, file.filename, file.file, file.content_type)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return FileOut(**rec.to_dict())


@router.delete("/files/{file_ref}", status_code=204)
def delete_file(file_ref: str, svc: FileService = Depends(get_service)):
    try:
        rec = svc.get_by_ref(file_ref)
        svc.delete(rec.id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    except _STORE_ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Object storage error: {e}")
    return Response(status_code=204)


@router.get("/folders", response_model=List[str])
def list_folders(category: Optional[str] = None,
                 svc: FileService = Depends(get_service)):
    return svc.list_folders(category)
