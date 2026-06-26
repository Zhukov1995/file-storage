# Universal File Storage Service — Design (MVP)

**Date:** 2026-06-26
**Status:** Draft for review

## 1. Purpose

A standalone, reusable file storage microservice with a REST API and Swagger,
plus a thin uploader UI embedded into Superset as a route (`/uploader/`). The storage service
holds **any** file type (IFC/BIM models, images, SVG, etc.); the file's *meaning*
is metadata, not a separate entity. BIM is simply `category = 'bim'`.

The service is designed to be **reusable across other projects** — it knows nothing
about Superset. Superset is just one client.

### Goals
- Standalone storage service: REST API + Swagger, runs in Docker.
- Universal: one `files` registry table; type is a `category` + flexible `metadata`.
- Folder organization (virtual folders over flat object storage).
- Full CRUD over files and metadata.
- A Superset page (`/uploader/`) that talks to the storage REST API.
- Runs and is testable locally via a single docker-compose.

### Non-Goals (YAGNI for this MVP)
- IFC→XKT conversion (later step).
- Extracting `global_id` / element properties (later step).
- The xeokit viewer plugin (separate project).
- Image thumbnails/previews, file versioning.
- Full JWT/OAuth on the storage service (API key is enough for MVP).

## 2. Architecture

Two independent parts communicating over HTTP REST.

```
┌──────────────────────────────────────────┐
│  file-storage-service (FastAPI)           │  standalone, reusable
│  • REST API + Swagger (/docs)             │
│  • Auth: static API key (X-API-Key)       │
│  • Streams files to MinIO                  │
│  • Registry in Postgres (files table)     │
└──────────────▲────────────────────────────┘
               │ HTTP REST (X-API-Key)
               │
┌──────────────┴────────────────────────────┐
│  Superset (Flask)                          │
│  • Custom Flask Blueprint → /uploader/     │  thin client
│  • Serves HTML/JS uploader page            │
│  • Server-side PROXY to storage API,       │
│    injecting the API key (key never        │
│    reaches the browser)                    │
└────────────────────────────────────────────┘
```

### Why this split
- **Reusability:** storage is its own product with its own Swagger; any project
  can use it via the same API.
- **Clean boundary:** Superset does not know about MinIO/Postgres of the storage —
  only its HTTP API.
- **Security:** the browser talks to the Superset blueprint, which proxies to the
  storage service and adds the API key server-side. No CORS, no key in the browser,
  reuses Superset's authentication (only logged-in users reach the page).
- **Local testability:** a single docker-compose brings up storage + MinIO +
  Postgres + Superset. Swagger (`/docs`) is reachable directly for manual testing
  with the API key.

### Docker services (docker-compose)
- `file-storage` — FastAPI app (this repo).
- `minio` — S3-compatible object storage.
- `postgres` — registry DB (the same DB Superset reads, so dashboards see `files`).
- `superset` — with the custom blueprint mounted and `superset_config.py` set.

## 3. Data Model — `files` table

Single universal registry table. Type-specific data lives in `metadata` (JSONB).

```sql
CREATE TABLE files (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,        -- human-readable name
    category      VARCHAR(64)  NOT NULL,        -- 'bim' | 'image' | 'svg' | ...
    folder        VARCHAR(512) NOT NULL DEFAULT '/',  -- virtual folder path, e.g. '/estate-1/building-A'
    tags          TEXT[]       DEFAULT '{}',    -- free-form labels
    object_key    VARCHAR(512) NOT NULL UNIQUE, -- key in MinIO
    file_name     VARCHAR(255) NOT NULL,        -- original filename
    file_size     BIGINT,                       -- bytes
    content_type  VARCHAR(127),                 -- MIME
    metadata      JSONB        NOT NULL DEFAULT '{}',  -- type-specific fields
    status        VARCHAR(32)  NOT NULL DEFAULT 'ready',  -- reserved for future processing
    created_at    TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE INDEX idx_files_category ON files(category);
CREATE INDEX idx_files_folder   ON files(folder);
CREATE INDEX idx_files_tags     ON files USING GIN(tags);
CREATE INDEX idx_files_metadata ON files USING GIN(metadata);
```

### Folders
- Folders are **virtual** — a `folder` string path on each row (like S3 prefixes).
  No separate folders table in MVP.
- Listing supports filtering by `folder` (exact) and by prefix (to list a subtree).
- `object_key` layout in MinIO: `{category}{folder}/{uuid}_{file_name}`
  (folder normalized, e.g. `bim/estate-1/building-A/<uuid>_model.ifc`).

## 4. Storage Service REST API

All endpoints require `X-API-Key`. Swagger at `/docs`.

| Method | Path | Action |
|--------|------|--------|
| `POST`   | `/files` | Upload file (multipart) + `name`, `category`, `folder`, `tags`, `metadata` |
| `GET`    | `/files` | List with filters `?category=&folder=&prefix=&tag=&q=` + pagination |
| `GET`    | `/files/{id}` | Metadata + presigned download URL |
| `GET`    | `/files/{id}/download` | Redirect to presigned URL (or stream) |
| `PATCH`  | `/files/{id}` | Update `name`/`category`/`folder`/`tags`/`metadata` |
| `PUT`    | `/files/{id}/content` | Replace the underlying file |
| `DELETE` | `/files/{id}` | Delete DB row **and** MinIO object |
| `GET`    | `/folders` | List distinct folders (for the UI tree) |
| `GET`    | `/healthz` | Liveness (no auth) |

### Category validation (extensible)
A config dict drives per-category rules so adding a type is a one-line change:

```python
CATEGORIES = {
    "bim":   {"ext": [".ifc"],                         "max_mb": 500},
    "image": {"ext": [".png", ".jpg", ".jpeg", ".webp"], "max_mb": 50},
    "svg":   {"ext": [".svg"],                          "max_mb": 5},
}
```
Unknown category or disallowed extension/oversize → `422` before streaming begins.

## 5. Key Flows

### Upload (POST /files)
1. Validate `category`, extension, and size against `CATEGORIES`.
2. Normalize `folder`, generate `object_key = {category}{folder}/{uuid}_{file_name}`.
3. **Stream** the file into MinIO (no full load into RAM).
4. Insert the `files` row.
5. Return JSON with `id` and metadata.

### Delete (DELETE /files/{id})
1. Read `object_key` from DB.
2. Delete the object from MinIO.
3. Delete the DB row.
4. If the MinIO object is missing, log and still delete the row (idempotent).

### Failure compensation
- If the file lands in MinIO but the DB insert fails → delete the uploaded object
  (no orphaned files).

## 6. Superset Blueprint (`/uploader/`)

- Registered via `superset_config.py` (`BLUEPRINTS = [uploader_bp]`).
- Routes:
  - `GET /uploader/` → serves the uploader HTML page (Jinja2).
  - `ANY /uploader/api/<path>` → server-side proxy to the storage service,
    injecting `X-API-Key` from Superset config/env.
- Browser JS calls only `/uploader/api/...` (same origin → no CORS, reuses
  Superset auth). The API key stays server-side.
- Page UI: folder tree + file list (filter by category/folder), upload form with
  category/folder/tags, rename/move/delete actions.

## 7. Error Handling

- Invalid category/extension/oversize → `422` (before upload streaming).
- Not found → `404`.
- Missing/invalid API key → `401`.
- MinIO failure on upload → compensate (delete partial object), `502`.
- Proxy errors surfaced to the page with a readable message.

## 8. Testing (TDD)

- **Unit:** CRUD logic, category validation, folder normalization (mock MinIO + DB).
- **Integration:** real MinIO + Postgres in a test compose; full cycle
  upload → list → get → patch → replace → delete.
- **Compensation:** simulate DB-insert failure after MinIO upload; assert no orphan.
- **Proxy:** blueprint forwards requests and injects the API key; key absent from
  responses sent to the browser.

## 9. Tech Stack

- Storage service: **Python + FastAPI**, `boto3`/`minio` client, SQLAlchemy,
  `python-multipart` for uploads, Pydantic models, auto Swagger.
- Superset integration: **Flask Blueprint** via `superset_config.py`, `requests`
  for server-side proxy.
- Infra: **MinIO**, **Postgres**, **docker-compose** for local + deploy.

## 10. Open Questions for Reviewer

- The registry DB is the same Postgres Superset reads (confirmed) — verify the
  exact DB/schema name during implementation.
- Should `folder` move be a dedicated endpoint or just a `PATCH` of `folder`?
  (MVP: plain `PATCH`.)
