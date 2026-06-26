# План реализации: универсальный сервис хранения файлов

> **Для агентов-исполнителей:** ОБЯЗАТЕЛЬНЫЙ СУБ-СКИЛЛ: используйте superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans, чтобы реализовать план задача за задачей. Шаги используют синтаксис чекбоксов (`- [ ]`) для отслеживания прогресса.

**Цель:** Построить отдельный переиспользуемый микросервис хранения файлов (FastAPI + REST + Swagger, на базе MinIO + Postgres) и встроить в Superset тонкую страницу-загрузчик по адресу `/uploader/`, которая проксирует запросы к сервису.

**Архитектура:** Две части, общающиеся по HTTP. (1) `file-storage` — самостоятельный FastAPI-сервис с единой универсальной таблицей-реестром `files`, виртуальными папками, валидацией по категориям, потоковой загрузкой в MinIO, защищённый статическим API-ключом. (2) Flask Blueprint в Superset по адресу `/uploader/`, который отдаёт HTML-страницу и на стороне сервера проксирует запросы браузера к сервису хранения, добавляя API-ключ, который никогда не попадает в браузер. Всё запускается локально через docker-compose.

**Стек технологий:** Python 3.9, FastAPI, Uvicorn, SQLAlchemy 2.x, Pydantic v2, `boto3` (S3/MinIO-клиент), `python-multipart`, `requests` (прокси), Postgres, MinIO, Docker Compose. Тесты — `pytest` + `httpx` TestClient + интеграция с реальным MinIO.

## Глобальные ограничения

- Сервис хранения **не зависит от Superset** — никаких импортов из `superset`, пригоден для любого проекта.
- Все эндпоинты хранилища (кроме `/healthz`) требуют заголовок `X-API-Key`. Неверный/отсутствует → `401`.
- Единая таблица-реестр `files`; тип файла — это `category` + `metadata` JSONB, а не отдельная таблица.
- Папки **виртуальные** — строковая колонка `folder`, без отдельной таблицы папок. `object_key` = `{category}{folder}/{uuid}_{file_name}`.
- Загрузки **стримятся** в MinIO (никогда не грузим файл целиком в RAM).
- Сбой загрузки после того, как объект уже попал в MinIO, должен **компенсироваться** (удаление осиротевшего объекта).
- Правила категорий берутся из единого конфиг-словаря `CATEGORIES`; добавление типа = одна строка.
- Blueprint в Superset монтируется на `/uploader/`; JS в браузере вызывает только `/uploader/api/...` (тот же origin, без CORS). API-ключ добавляется на стороне сервера.
- Реестр живёт в том же Postgres, который читает Superset (чтобы дашборды могли запрашивать `files`).
- Корень проекта сервиса хранения: `services/file-storage/` (новый). Клон Superset лежит в `superset_tech/`.
- Целевой Python: 3.9 (на хосте 3.9.18). Избегать синтаксиса только для 3.10+ (`X | Y` в аннотациях, вычисляемых в рантайме) — использовать `Optional[...]`.

---

### Задача 1: Скелет сервиса хранения + healthz

**Файлы:**
- Создать: `services/file-storage/pyproject.toml`
- Создать: `services/file-storage/app/__init__.py`
- Создать: `services/file-storage/app/main.py`
- Создать: `services/file-storage/app/config.py`
- Тест: `services/file-storage/tests/test_health.py`

**Интерфейсы:**
- Предоставляет: `app.main:create_app() -> FastAPI`; FastAPI app exposes `GET /healthz` → `{"status": "ok"}` (no auth). `app.config:Settings` Pydantic-settings object reading env: `DATABASE_URL`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `API_KEY`.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_health.py
from fastapi.testclient import TestClient
from app.main import create_app


def test_healthz_ok():
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_health.py -v`
Ожидается: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Шаг 3: Написать минимальную реализацию**

```toml
# services/file-storage/pyproject.toml
[project]
name = "file-storage"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "pydantic>=2.6",
    "pydantic-settings>=2.2",
    "boto3>=1.34",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "requests>=2.31"]

[tool.pytest.ini_options]
pythonpath = ["."]
```

```python
# services/file-storage/app/__init__.py
```

```python
# services/file-storage/app/config.py
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://superset:superset@localhost:5432/superset"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "files"
    s3_region: str = "us-east-1"
    api_key: str = "dev-key"


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

```python
# services/file-storage/app/main.py
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Universal File Storage", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && pip install -e ".[dev]" && python -m pytest tests/test_health.py -v`
Ожидается: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): service skeleton with healthz endpoint"
```

---

### Задача 2: Зависимость аутентификации по API-ключу

**Файлы:**
- Создать: `services/file-storage/app/auth.py`
- Изменить: `services/file-storage/app/main.py`
- Тест: `services/file-storage/tests/test_auth.py`

**Интерфейсы:**
- Предоставляет: `app.auth:require_api_key` — a FastAPI dependency that reads header `X-API-Key`, compares to `Settings.api_key`, raises `HTTPException(401)` on mismatch/absence. Returns `None` on success. A temporary `GET /_protected` route is added to `main.py` to exercise it; later tasks attach `require_api_key` to real routes.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_auth.py
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import get_settings

client = TestClient(create_app())


def test_protected_requires_key():
    resp = client.get("/_protected")
    assert resp.status_code == 401


def test_protected_rejects_wrong_key():
    resp = client.get("/_protected", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_protected_accepts_correct_key():
    key = get_settings().api_key
    resp = client.get("/_protected", headers={"X-API-Key": key})
    assert resp.status_code == 200
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_auth.py -v`
Ожидается: FAIL (404 on `/_protected`, route not defined)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/auth.py
from fastapi import Header, HTTPException, status
from typing import Optional
from app.config import get_settings


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = get_settings().api_key
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
```

```python
# services/file-storage/app/main.py  (add inside create_app, before `return app`)
    from fastapi import Depends
    from app.auth import require_api_key

    @app.get("/_protected", dependencies=[Depends(require_api_key)])
    def _protected() -> dict:
        return {"ok": True}
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_auth.py -v`
Ожидается: PASS (3 passed)

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): X-API-Key auth dependency"
```

---

### Задача 3: Модель БД + сессия

**Файлы:**
- Создать: `services/file-storage/app/db.py`
- Создать: `services/file-storage/app/models.py`
- Тест: `services/file-storage/tests/test_models.py`

**Интерфейсы:**
- Предоставляет:
  - `app.db:Base` (SQLAlchemy declarative base), `app.db:engine`, `app.db:SessionLocal`, `app.db:get_db()` generator dependency, `app.db:init_db()` (create_all).
  - `app.models:FileRecord` with columns: `id:int`, `name:str`, `category:str`, `folder:str='/'`, `tags:list[str]`, `object_key:str` (unique), `file_name:str`, `file_size:int`, `content_type:str`, `metadata_:dict` (mapped to column `metadata`), `status:str='ready'`, `created_at`, `updated_at`. Method `to_dict() -> dict` (keys: id, name, category, folder, tags, object_key, file_name, file_size, content_type, metadata, status, created_at, updated_at as ISO strings).
- Tests use SQLite in-memory; JSONB/ARRAY degrade via `JSON`/`Text`-compatible types — use `sqlalchemy.JSON` and a portable tags type (see impl).

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_models.py
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
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_models.py -v`
Ожидается: FAIL (`No module named 'app.db'`)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings

Base = declarative_base()
engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(engine)
```

```python
# services/file-storage/app/models.py
from datetime import datetime
from typing import List
from sqlalchemy import String, BigInteger, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, Text
import json
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
```

> Примечание: production uses Postgres; `TagList`/`JSON` work on both. A later migration can switch to native `ARRAY`/`JSONB` + GIN indexes (spec §3) without changing `to_dict`.

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_models.py -v`
Ожидается: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): files registry model and DB session"
```

---

### Задача 4: Валидация категорий + хелперы папок/ключей

**Файлы:**
- Создать: `services/file-storage/app/categories.py`
- Создать: `services/file-storage/app/keys.py`
- Тест: `services/file-storage/tests/test_categories.py`
- Тест: `services/file-storage/tests/test_keys.py`

**Интерфейсы:**
- Предоставляет:
  - `app.categories:CATEGORIES` dict; `app.categories:validate(category:str, filename:str, size_bytes:int) -> None` raises `ValueError` on unknown category / bad extension / oversize.
  - `app.keys:normalize_folder(folder:Optional[str]) -> str` → always leading slash, no trailing slash (except root `/`), collapses dupes.
  - `app.keys:build_object_key(category:str, folder:str, filename:str) -> str` → `f"{category}{folder}/{uuid4().hex}_{safe_name}"` with root folder producing no double slash.

- [ ] **Шаг 1: Написать падающие тесты**

```python
# services/file-storage/tests/test_categories.py
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
```

```python
# services/file-storage/tests/test_keys.py
from app.keys import normalize_folder, build_object_key


def test_normalize_root():
    assert normalize_folder(None) == "/"
    assert normalize_folder("") == "/"
    assert normalize_folder("/") == "/"


def test_normalize_strips_trailing():
    assert normalize_folder("/a/b/") == "/a/b"
    assert normalize_folder("a/b") == "/a/b"


def test_build_key_root():
    key = build_object_key("bim", "/", "model.ifc")
    assert key.startswith("bim/")
    assert key.endswith("_model.ifc")
    assert "//" not in key


def test_build_key_nested():
    key = build_object_key("bim", "/estate-1", "model.ifc")
    assert key.startswith("bim/estate-1/")
    assert "//" not in key
```

- [ ] **Шаг 2: Запустить тесты и убедиться, что они падают**

Запуск: `cd services/file-storage && python -m pytest tests/test_categories.py tests/test_keys.py -v`
Ожидается: FAIL (`No module named 'app.categories'`)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/categories.py
import os

CATEGORIES = {
    "bim":   {"ext": [".ifc"], "max_mb": 500},
    "image": {"ext": [".png", ".jpg", ".jpeg", ".webp"], "max_mb": 50},
    "svg":   {"ext": [".svg"], "max_mb": 5},
}


def validate(category: str, filename: str, size_bytes: int) -> None:
    rule = CATEGORIES.get(category)
    if rule is None:
        raise ValueError(f"Unknown category: {category}")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in rule["ext"]:
        raise ValueError(f"Extension {ext} not allowed for category {category}")
    if size_bytes > rule["max_mb"] * 1024 * 1024:
        raise ValueError(f"File exceeds {rule['max_mb']} MB limit")
```

```python
# services/file-storage/app/keys.py
import re
from typing import Optional
from uuid import uuid4

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_folder(folder: Optional[str]) -> str:
    if not folder or folder == "/":
        return "/"
    parts = [p for p in folder.split("/") if p]
    return "/" + "/".join(parts)


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_") or "file"


def build_object_key(category: str, folder: str, filename: str) -> str:
    folder = normalize_folder(folder)
    prefix = category if folder == "/" else f"{category}{folder}"
    return f"{prefix}/{uuid4().hex}_{_safe_name(filename)}"
```

- [ ] **Шаг 4: Запустить тесты и убедиться, что они проходят**

Запуск: `cd services/file-storage && python -m pytest tests/test_categories.py tests/test_keys.py -v`
Ожидается: PASS

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): category validation and object-key helpers"
```

---

### Задача 5: Клиент хранилища MinIO (стриминг + удаление) — интеграционный тест

**Файлы:**
- Создать: `services/file-storage/app/storage.py`
- Тест: `services/file-storage/tests/test_storage_integration.py`
- Создать: `services/file-storage/tests/conftest.py`

**Интерфейсы:**
- Предоставляет: `app.storage:ObjectStore` class wrapping boto3 S3 client:
  - `ensure_bucket() -> None`
  - `put_stream(object_key:str, fileobj, content_type:str) -> int` (returns bytes written; streams via `upload_fileobj`)
  - `delete(object_key:str) -> None` (idempotent — no error if absent)
  - `presigned_get(object_key:str, expires:int=3600) -> str`
  - `exists(object_key:str) -> bool`
- `conftest.py` provides a `store` fixture pointing at the docker-compose MinIO; tests are marked `integration` and skipped when `S3_ENDPOINT` is unreachable.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/conftest.py
import io
import os
import pytest
from app.storage import ObjectStore


def _minio_up() -> bool:
    import socket
    from urllib.parse import urlparse
    ep = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    host = urlparse(ep).hostname
    port = urlparse(ep).port or 9000
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


@pytest.fixture
def store():
    if not _minio_up():
        pytest.skip("MinIO not reachable")
    s = ObjectStore()
    s.ensure_bucket()
    return s


@pytest.fixture
def sample():
    return io.BytesIO(b"hello-ifc-bytes")
```

```python
# services/file-storage/tests/test_storage_integration.py
import io


def test_put_exists_presign_delete(store, sample):
    key = "image/test/_unit.png"
    n = store.put_stream(key, sample, "image/png")
    assert n == len(b"hello-ifc-bytes")
    assert store.exists(key) is True
    url = store.presigned_get(key)
    assert key in url
    store.delete(key)
    assert store.exists(key) is False


def test_delete_is_idempotent(store):
    store.delete("image/does/not/_exist.png")  # no raise
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_storage_integration.py -v`
Ожидается: FAIL (`No module named 'app.storage'`). (If MinIO is down, tests skip — that's expected until Задача 9 brings up compose; run again after.)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/storage.py
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.config import get_settings


class ObjectStore:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def put_stream(self, object_key: str, fileobj, content_type: str) -> int:
        start = fileobj.tell()
        self._client.upload_fileobj(
            fileobj, self._bucket, object_key,
            ExtraArgs={"ContentType": content_type or "application/octet-stream"},
        )
        head = self._client.head_object(Bucket=self._bucket, Key=object_key)
        return int(head["ContentLength"])

    def delete(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)

    def exists(self, object_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError:
            return False

    def presigned_get(self, object_key: str, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires,
        )
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Run (after `docker compose -f docker-compose.storage.yml up -d minio` from Task 9, or any reachable MinIO): `cd services/file-storage && python -m pytest tests/test_storage_integration.py -v`
Ожидается: PASS (or SKIP if MinIO intentionally down). Must PASS at least once with MinIO up before Задача 6 is considered complete in review.

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): MinIO object store wrapper"
```

---

### Задача 6: Слой CRUD-сервиса (БД + оркестрация хранилища, с компенсацией)

**Файлы:**
- Создать: `services/file-storage/app/service.py`
- Тест: `services/file-storage/tests/test_service.py`

**Интерфейсы:**
- Использует: `FileRecord` (Задача 3), `categories.validate` (Задача 4), `keys.build_object_key` (Задача 4), `ObjectStore` (Задача 5).
- Produces `app.service:FileService(db, store)` with:
  - `create(name, category, folder, tags, metadata, filename, fileobj, content_type) -> FileRecord` — validates, builds key, streams to store, inserts row; on DB failure deletes the uploaded object then re-raises.
  - `list(category=None, folder=None, prefix=None, tag=None, q=None, limit=50, offset=0) -> list[FileRecord]`
  - `get(id) -> FileRecord` (raises `KeyError` if missing)
  - `update(id, **fields) -> FileRecord` (name/category/folder/tags/metadata)
  - `replace_content(id, filename, fileobj, content_type) -> FileRecord` (uploads new object, updates row, deletes old object)
  - `delete(id) -> None` (delete object then row; idempotent on missing object)
  - `list_folders(category=None) -> list[str]`
- Tests use SQLite session + a **fake store** (in-memory dict) to stay unit-level; compensation tested by injecting a failing DB.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_service.py
import io
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.service import FileService


class FakeStore:
    def __init__(self): self.objs = {}
    def ensure_bucket(self): pass
    def put_stream(self, key, fileobj, ct):
        data = fileobj.read(); self.objs[key] = data; return len(data)
    def delete(self, key): self.objs.pop(key, None)
    def exists(self, key): return key in self.objs
    def presigned_get(self, key, expires=3600): return f"http://minio/{key}"


def make_db():
    eng = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def svc():
    return FileService(make_db(), FakeStore())


def test_create_and_get():
    s = svc()
    rec = s.create("A", "bim", "/estate-1", ["t"], {}, "model.ifc",
                   io.BytesIO(b"data"), "application/x-ifc")
    assert rec.id is not None
    assert s.store.exists(rec.object_key)
    got = s.get(rec.id)
    assert got.name == "A"


def test_create_rejects_bad_category():
    s = svc()
    with pytest.raises(ValueError):
        s.create("A", "nope", "/", [], {}, "x.ifc", io.BytesIO(b"d"), "x")


def test_delete_removes_object_and_row():
    s = svc()
    rec = s.create("A", "svg", "/", [], {}, "i.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")
    key = rec.object_key
    s.delete(rec.id)
    assert not s.store.exists(key)
    with pytest.raises(KeyError):
        s.get(rec.id)


def test_list_filters_by_category():
    s = svc()
    s.create("A", "bim", "/", [], {}, "a.ifc", io.BytesIO(b"d"), "x")
    s.create("B", "svg", "/", [], {}, "b.svg", io.BytesIO(b"<svg/>"), "x")
    res = s.list(category="bim")
    assert len(res) == 1 and res[0].name == "A"


def test_compensation_on_db_failure():
    s = svc()
    # force DB failure by closing the session's connection mid-flight
    orig_commit = s.db.commit
    def boom(): raise RuntimeError("db down")
    s.db.commit = boom
    with pytest.raises(RuntimeError):
        s.create("A", "bim", "/", [], {}, "a.ifc", io.BytesIO(b"d"), "x")
    # object must have been compensated (deleted)
    assert s.store.objs == {}
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_service.py -v`
Ожидается: FAIL (`No module named 'app.service'`)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/service.py
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
        data = fileobj.read()
        validate(category, filename, len(data))
        import io as _io
        key = build_object_key(category, folder, filename)
        size = self.store.put_stream(key, _io.BytesIO(data), content_type)
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
        self.db.commit()
        self.db.refresh(rec)
        return rec

    def replace_content(self, id, filename, fileobj, content_type) -> FileRecord:
        rec = self.get(id)
        data = fileobj.read()
        validate(rec.category, filename, len(data))
        import io as _io
        old_key = rec.object_key
        new_key = build_object_key(rec.category, rec.folder, filename)
        size = self.store.put_stream(new_key, _io.BytesIO(data), content_type)
        rec.object_key = new_key
        rec.file_name = filename
        rec.file_size = size
        rec.content_type = content_type
        self.db.commit()
        self.db.refresh(rec)
        self.store.delete(old_key)
        return rec

    def delete(self, id: int) -> None:
        rec = self.get(id)
        key = rec.object_key
        self.store.delete(key)
        self.db.delete(rec)
        self.db.commit()

    def list_folders(self, category: Optional[str] = None) -> List[str]:
        stmt = select(FileRecord.folder).distinct()
        if category:
            stmt = stmt.where(FileRecord.category == category)
        return sorted(r[0] for r in self.db.execute(stmt).all())
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_service.py -v`
Ожидается: PASS (5 passed)

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): CRUD service layer with upload compensation"
```

---

### Задача 7: REST-роуты + Pydantic-схемы

**Файлы:**
- Создать: `services/file-storage/app/schemas.py`
- Создать: `services/file-storage/app/routes.py`
- Изменить: `services/file-storage/app/main.py`
- Тест: `services/file-storage/tests/test_routes.py`

**Интерфейсы:**
- Использует: `FileService` (Задача 6), `require_api_key` (Задача 2), `get_db` (Задача 3).
- Предоставляет: router mounted at `/files` plus `/folders`, all under `Depends(require_api_key)`. Routes per spec §4. Response model `FileOut` mirrors `to_dict()`. `GET /files/{id}` includes `download_url` (presigned). Uses a `get_service` dependency that builds `FileService(db, ObjectStore())`. Tests override `get_service` with the FakeStore + SQLite from Task 6.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_routes.py
import io
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import create_app
from app.db import Base, get_db
from app.routes import get_service
from app.service import FileService
from app.config import get_settings
from tests.test_service import FakeStore

eng = create_engine("sqlite+pysqlite:///:memory:")
Base.metadata.create_all(eng)
TestingSession = sessionmaker(bind=eng)
_store = FakeStore()

app = create_app()


def _svc_override():
    db = TestingSession()
    try:
        yield FileService(db, _store)
    finally:
        db.close()


app.dependency_overrides[get_service] = _svc_override
client = TestClient(app)
KEY = {"X-API-Key": get_settings().api_key}


def test_upload_requires_key():
    resp = client.post("/files", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")},
                       data={"name": "A", "category": "svg"})
    assert resp.status_code == 401


def test_upload_then_list_get_delete():
    resp = client.post(
        "/files", headers=KEY,
        files={"file": ("a.svg", b"<svg/>", "image/svg+xml")},
        data={"name": "Icon", "category": "svg", "folder": "/icons", "tags": "ui,small"},
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    assert resp.json()["folder"] == "/icons"
    assert resp.json()["tags"] == ["ui", "small"]

    lst = client.get("/files", headers=KEY, params={"category": "svg"})
    assert lst.status_code == 200 and len(lst.json()) == 1

    one = client.get(f"/files/{fid}", headers=KEY)
    assert one.status_code == 200 and "download_url" in one.json()

    dele = client.delete(f"/files/{fid}", headers=KEY)
    assert dele.status_code == 204
    assert client.get(f"/files/{fid}", headers=KEY).status_code == 404


def test_upload_bad_category_422():
    resp = client.post(
        "/files", headers=KEY,
        files={"file": ("a.ifc", b"x", "application/x-ifc")},
        data={"name": "A", "category": "nope"},
    )
    assert resp.status_code == 422
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_routes.py -v`
Ожидается: FAIL (`No module named 'app.routes'`)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# services/file-storage/app/schemas.py
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
```

```python
# services/file-storage/app/routes.py
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Response
from app.auth import require_api_key
from app.db import get_db
from app.storage import ObjectStore
from app.service import FileService
from app.schemas import FileOut, FilePatch

router = APIRouter(dependencies=[Depends(require_api_key)])


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
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    out = rec.to_dict()
    out["download_url"] = svc.store.presigned_get(rec.object_key)
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
    return FileOut(**rec.to_dict())


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: int, svc: FileService = Depends(get_service)):
    try:
        svc.delete(file_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(status_code=204)


@router.get("/folders", response_model=List[str])
def list_folders(category: Optional[str] = None,
                 svc: FileService = Depends(get_service)):
    return svc.list_folders(category)
```

```python
# services/file-storage/app/main.py  (replace create_app body to include router + init)
from fastapi import FastAPI, Depends
from app.auth import require_api_key
from app.routes import router
from app.db import init_db


def create_app() -> FastAPI:
    app = FastAPI(title="Universal File Storage", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/_protected", dependencies=[Depends(require_api_key)])
    def _protected() -> dict:
        return {"ok": True}

    @app.on_event("startup")
    def _startup() -> None:
        try:
            init_db()
        except Exception:
            pass  # DB may be unavailable in unit tests; routes are overridden there

    app.include_router(router)
    return app


app = create_app()
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_routes.py -v`
Ожидается: PASS (3 passed)

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): REST routes and schemas for files CRUD"
```

---

### Задача 7.5: Автономная HTML тест-страница на корне сервиса

**Файлы:**
- Создать: `services/file-storage/app/static/index.html`
- Изменить: `services/file-storage/app/main.py`
- Тест: `services/file-storage/tests/test_index_page.py`

**Интерфейсы:**
- Использует: `create_app()` (Задача 1/7), эндпоинты `/files`, `/folders` (Задача 7).
- Предоставляет: `GET /` отдаёт `index.html` (без auth — это статика страницы; сами вызовы API со страницы передают `X-API-Key`). Страница — автономный тест-UI: ввод API-ключа, форма загрузки (name/category/folder/tags/file), список с фильтром по категории, кнопки удаления. Ходит в собственный REST API сервиса. Эта вёрстка переиспользуется в Задаче 10 (страница Superset), но там ключ инжектится прокси-сервером, а здесь вводится вручную в поле.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_index_page.py
from fastapi.testclient import TestClient
from app.main import create_app

client = TestClient(create_app())


def test_index_served_without_auth():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "File Storage" in resp.text


def test_index_references_api():
    resp = client.get("/")
    # страница должна обращаться к /files через fetch
    assert "/files" in resp.text
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_index_page.py -v`
Ожидается: FAIL (404 на `/`, страница не определена)

- [ ] **Шаг 3: Написать минимальную реализацию**

```html
<!-- services/file-storage/app/static/index.html -->
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>File Storage — тест-страница</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 900px; }
    h1 { margin-bottom: .3rem; }
    .key, form.upload { display: grid; gap: .5rem; max-width: 480px; margin-bottom: 1rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; }
    button { cursor: pointer; }
    .muted { color: #777; font-size: .85rem; }
  </style>
</head>
<body>
  <h1>File Storage</h1>
  <p class="muted">Автономная тест-страница сервиса. Введите API-ключ, затем загружайте и управляйте файлами.</p>

  <div class="key">
    <label>API-ключ:
      <input id="apikey" placeholder="change-me-dev-key" value="change-me-dev-key" />
    </label>
  </div>

  <form class="upload" id="up">
    <input name="name" placeholder="Отображаемое имя" required />
    <select name="category">
      <option value="bim">bim</option>
      <option value="image">image</option>
      <option value="svg">svg</option>
    </select>
    <input name="folder" placeholder="/папка/путь" value="/" />
    <input name="tags" placeholder="теги,через,запятую" />
    <input type="file" name="file" required />
    <button type="submit">Загрузить</button>
  </form>

  <label>Фильтр по категории:
    <select id="filter">
      <option value="">все</option>
      <option value="bim">bim</option>
      <option value="image">image</option>
      <option value="svg">svg</option>
    </select>
  </label>

  <table id="list"><thead><tr>
    <th>ID</th><th>Имя</th><th>Категория</th><th>Папка</th><th>Теги</th><th></th>
  </tr></thead><tbody></tbody></table>

  <script>
    const key = () => document.getElementById('apikey').value;
    const api = (p, opt = {}) => fetch('/' + p, {
      ...opt,
      headers: { ...(opt.headers || {}), 'X-API-Key': key() },
    });
    async function refresh() {
      const cat = document.getElementById('filter').value;
      const r = await api('files' + (cat ? `?category=${cat}` : ''));
      if (!r.ok) { alert('Ошибка списка: ' + (await r.text())); return; }
      const rows = await r.json();
      const tb = document.querySelector('#list tbody');
      tb.innerHTML = '';
      for (const f of rows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${f.id}</td><td>${f.name}</td><td>${f.category}</td>
          <td>${f.folder}</td><td>${(f.tags||[]).join(', ')}</td>
          <td><button data-id="${f.id}">удалить</button></td>`;
        tr.querySelector('button').onclick = async () => {
          await api('files/' + f.id, { method: 'DELETE' });
          refresh();
        };
        tb.appendChild(tr);
      }
    }
    document.getElementById('up').onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const r = await api('files', { method: 'POST', body: fd });
      if (!r.ok) { alert('Ошибка загрузки: ' + (await r.text())); return; }
      e.target.reset();
      refresh();
    };
    document.getElementById('filter').onchange = refresh;
    refresh();
  </script>
</body>
</html>
```

В `app/main.py` добавить отдачу страницы. Внутри `create_app()`, перед `app.include_router(router)`:

```python
    from pathlib import Path
    from fastapi.responses import HTMLResponse

    _INDEX = Path(__file__).parent / "static" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX.read_text(encoding="utf-8")
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_index_page.py -v && python -m pytest tests/ -v`
Ожидается: PASS (2 passed для страницы; весь набор тоже зелёный)

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage
git commit -m "feat(storage): standalone HTML test page at root"
```

---

### Задача 8: Dockerfile сервиса хранения

**Файлы:**
- Создать: `services/file-storage/Dockerfile`
- Создать: `services/file-storage/.dockerignore`
- Создать: `services/file-storage/requirements.txt`

**Интерфейсы:**
- Предоставляет: a container that runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Consumed by Задача 9 compose as service `file-storage`.

- [ ] **Шаг 1: Создать requirements.txt (зафиксировать зависимости из pyproject)**

```text
# services/file-storage/requirements.txt
fastapi>=0.110
uvicorn[standard]>=0.29
sqlalchemy>=2.0
psycopg2-binary>=2.9
pydantic>=2.6
pydantic-settings>=2.2
boto3>=1.34
python-multipart>=0.0.9
```

- [ ] **Шаг 2: Создать Dockerfile**

```dockerfile
# services/file-storage/Dockerfile
FROM python:3.11-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```text
# services/file-storage/.dockerignore
tests/
__pycache__/
*.pyc
.env
.pytest_cache/
```

- [ ] **Шаг 3: Собрать образ для проверки**

Запуск: `cd services/file-storage && docker build -t file-storage:dev .`
Ожидается: build succeeds, final image created.

- [ ] **Шаг 4: Smoke-проверка healthz**

Запуск:
```bash
docker run -d --name fs-smoke -p 8001:8000 file-storage:dev
sleep 3
curl -s localhost:8001/healthz
docker rm -f fs-smoke
```
Ожидается: `{"status":"ok"}`

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage/Dockerfile services/file-storage/.dockerignore services/file-storage/requirements.txt
git commit -m "feat(storage): Dockerfile for file-storage service"
```

---

### Задача 9: docker-compose (MinIO + Postgres + хранилище)

**Файлы:**
- Создать: `services/file-storage/docker-compose.storage.yml`
- Создать: `services/file-storage/.env.example`

**Интерфейсы:**
- Produces a local stack: `minio` (9000/9001), `postgres` (5432, db `superset`), `file-storage` (8000). Used to run integration tests (Задача 5) and to back the Superset proxy (Задача 11). `MINIO`/DB creds match `.env.example`.

- [ ] **Шаг 1: Создать .env.example**

```text
# services/file-storage/.env.example
DATABASE_URL=postgresql+psycopg2://superset:superset@postgres:5432/superset
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=files
API_KEY=change-me-dev-key
```

- [ ] **Шаг 2: Создать compose-файл**

```yaml
# services/file-storage/docker-compose.storage.yml
services:
  minio:
    image: minio/minio:latest
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: ["minio_data:/data"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 10

  postgres:
    image: postgres:15
    restart: unless-stopped
    environment:
      POSTGRES_USER: superset
      POSTGRES_PASSWORD: superset
      POSTGRES_DB: superset
    ports: ["5432:5432"]
    volumes: ["pg_data:/var/lib/postgresql/data"]

  file-storage:
    build: .
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg2://superset:superset@postgres:5432/superset
      S3_ENDPOINT: http://minio:9000
      S3_ACCESS_KEY: minioadmin
      S3_SECRET_KEY: minioadmin
      S3_BUCKET: files
      API_KEY: change-me-dev-key
    ports: ["8000:8000"]
    depends_on:
      minio: {condition: service_healthy}
      postgres: {condition: service_started}

volumes:
  minio_data:
  pg_data:
```

- [ ] **Шаг 3: Поднять стек**

Запуск:
```bash
cd services/file-storage && cp -n .env.example .env
docker compose -f docker-compose.storage.yml up -d --build
sleep 8
curl -s localhost:8000/healthz
```
Ожидается: `{"status":"ok"}`

- [ ] **Шаг 4: Прогнать интеграционный тест против живого MinIO + end-to-end curl**

Запуск:
```bash
cd services/file-storage && S3_ENDPOINT=http://localhost:9000 \
  DATABASE_URL=postgresql+psycopg2://superset:superset@localhost:5432/superset \
  python -m pytest tests/test_storage_integration.py -v
# end-to-end upload via API
curl -s -X POST localhost:8000/files \
  -H "X-API-Key: change-me-dev-key" \
  -F name=Icon -F category=svg -F folder=/icons \
  -F file=@/dev/stdin;type=image/svg+xml <<< '<svg/>'
```
Ожидается: integration tests PASS; upload returns JSON with an `id`.

- [ ] **Шаг 5: Коммит**

```bash
git add services/file-storage/docker-compose.storage.yml services/file-storage/.env.example
git commit -m "feat(storage): docker-compose with MinIO and Postgres"
```

---

### Задача 10: Страница-загрузчик Superset + прокси-блупринт (Python)

**Файлы:**
- Создать: `superset_tech/docker/pythonpath_dev/uploader/__init__.py`
- Создать: `superset_tech/docker/pythonpath_dev/uploader/blueprint.py`
- Создать: `superset_tech/docker/pythonpath_dev/uploader/templates/uploader.html`
- Тест: `services/file-storage/tests/test_blueprint_proxy.py` (lives with storage tests; imports the blueprint module by path)

**Интерфейсы:**
- Использует: storage REST API (Задача 7) via `requests`, reading `STORAGE_BASE_URL` and `STORAGE_API_KEY` from env.
- Предоставляет: `uploader.blueprint:uploader_bp` Flask Blueprint, `url_prefix="/uploader"`:
  - `GET /uploader/` → renders `uploader.html`.
  - `GET|POST|PATCH|PUT|DELETE /uploader/api/<path:subpath>` → forwards to `{STORAGE_BASE_URL}/<subpath>` with `X-API-Key` injected, streaming body and query string; returns upstream status/body. Browser never sees the key.

- [ ] **Шаг 1: Написать падающий тест**

```python
# services/file-storage/tests/test_blueprint_proxy.py
import importlib.util
import os
from pathlib import Path
import flask
import pytest

BP_PATH = Path(__file__).resolve().parents[3] / \
    "superset_tech/docker/pythonpath_dev/uploader/blueprint.py"


def load_bp():
    spec = importlib.util.spec_from_file_location("uploader.blueprint", BP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(monkeypatch):
    os.environ["STORAGE_BASE_URL"] = "http://storage.test"
    os.environ["STORAGE_API_KEY"] = "secret-key"
    mod = load_bp()
    app = flask.Flask(__name__)
    app.register_blueprint(mod.uploader_bp)
    return app.test_client(), mod


def test_index_renders(client):
    c, _ = client
    resp = c.get("/uploader/")
    assert resp.status_code == 200
    assert b"Uploader" in resp.data


def test_proxy_injects_key_and_forwards(client, monkeypatch):
    c, mod = client
    captured = {}

    class FakeResp:
        status_code = 200
        content = b'[]'
        headers = {"Content-Type": "application/json"}

    def fake_request(method, url, headers=None, params=None, data=None, files=None, stream=False):
        captured.update(method=method, url=url, headers=headers, params=params)
        return FakeResp()

    monkeypatch.setattr(mod.requests, "request", fake_request)
    resp = c.get("/uploader/api/files?category=bim")
    assert resp.status_code == 200
    assert captured["url"] == "http://storage.test/files"
    assert captured["headers"]["X-API-Key"] == "secret-key"
    assert captured["params"]["category"] == "bim"
```

- [ ] **Шаг 2: Запустить тест и убедиться, что он падает**

Запуск: `cd services/file-storage && python -m pytest tests/test_blueprint_proxy.py -v`
Ожидается: FAIL (blueprint file does not exist → import error)

- [ ] **Шаг 3: Написать минимальную реализацию**

```python
# superset_tech/docker/pythonpath_dev/uploader/__init__.py
from .blueprint import uploader_bp  # noqa: F401
```

```python
# superset_tech/docker/pythonpath_dev/uploader/blueprint.py
import os
from flask import Blueprint, render_template, request, Response
import requests

uploader_bp = Blueprint(
    "uploader",
    __name__,
    url_prefix="/uploader",
    template_folder="templates",
)


def _storage_base() -> str:
    return os.environ.get("STORAGE_BASE_URL", "http://file-storage:8000").rstrip("/")


def _api_key() -> str:
    return os.environ.get("STORAGE_API_KEY", "change-me-dev-key")


@uploader_bp.route("/")
def index():
    return render_template("uploader.html")


@uploader_bp.route("/api/<path:subpath>",
                   methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
def proxy(subpath: str):
    url = f"{_storage_base()}/{subpath}"
    headers = {"X-API-Key": _api_key()}
    # forward multipart/body as-is
    files = None
    data = None
    if request.files:
        files = {k: (f.filename, f.stream, f.mimetype)
                 for k, f in request.files.items()}
        data = request.form.to_dict()
    elif request.data:
        data = request.get_data()
        if request.content_type:
            headers["Content-Type"] = request.content_type

    upstream = requests.request(
        request.method, url, headers=headers,
        params=request.args.to_dict(flat=True),
        data=data, files=files, stream=True,
    )
    resp_headers = {}
    if "Content-Type" in upstream.headers:
        resp_headers["Content-Type"] = upstream.headers["Content-Type"]
    return Response(upstream.content, status=upstream.status_code,
                    headers=resp_headers)
```

```html
<!-- superset_tech/docker/pythonpath_dev/uploader/templates/uploader.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>File Uploader</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; }
    form.upload { display: grid; gap: .5rem; max-width: 480px; }
    button { cursor: pointer; }
  </style>
</head>
<body>
  <h1>Uploader</h1>
  <form class="upload" id="up">
    <input name="name" placeholder="Display name" required />
    <select name="category">
      <option value="bim">bim</option>
      <option value="image">image</option>
      <option value="svg">svg</option>
    </select>
    <input name="folder" placeholder="/folder/path" value="/" />
    <input name="tags" placeholder="comma,separated,tags" />
    <input type="file" name="file" required />
    <button type="submit">Upload</button>
  </form>

  <label>Filter category:
    <select id="filter">
      <option value="">all</option>
      <option value="bim">bim</option>
      <option value="image">image</option>
      <option value="svg">svg</option>
    </select>
  </label>

  <table id="list"><thead><tr>
    <th>ID</th><th>Name</th><th>Category</th><th>Folder</th><th>Tags</th><th></th>
  </tr></thead><tbody></tbody></table>

  <script>
    const api = (p, opt) => fetch('/uploader/api/' + p, opt);
    async function refresh() {
      const cat = document.getElementById('filter').value;
      const r = await api('files' + (cat ? `?category=${cat}` : ''));
      const rows = await r.json();
      const tb = document.querySelector('#list tbody');
      tb.innerHTML = '';
      for (const f of rows) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${f.id}</td><td>${f.name}</td><td>${f.category}</td>
          <td>${f.folder}</td><td>${(f.tags||[]).join(', ')}</td>
          <td><button data-id="${f.id}">delete</button></td>`;
        tr.querySelector('button').onclick = async () => {
          await api('files/' + f.id, { method: 'DELETE' });
          refresh();
        };
        tb.appendChild(tr);
      }
    }
    document.getElementById('up').onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const r = await api('files', { method: 'POST', body: fd });
      if (!r.ok) { alert('Upload failed: ' + (await r.text())); return; }
      e.target.reset();
      refresh();
    };
    document.getElementById('filter').onchange = refresh;
    refresh();
  </script>
</body>
</html>
```

- [ ] **Шаг 4: Запустить тест и убедиться, что он проходит**

Запуск: `cd services/file-storage && python -m pytest tests/test_blueprint_proxy.py -v`
Ожидается: PASS (2 passed). (Requires `flask` + `requests` installed in the storage dev env: `pip install flask requests`.)

- [ ] **Шаг 5: Коммит**

```bash
git add superset_tech/docker/pythonpath_dev/uploader services/file-storage/tests/test_blueprint_proxy.py
git commit -m "feat(uploader): Superset blueprint with proxy and upload page"
```

---

### Задача 11: Регистрация блупринта в конфиге Superset + проводка в compose

**Файлы:**
- Изменить: `superset_tech/docker/pythonpath_dev/superset_config.py`
- Изменить: `superset_tech/docker-compose-non-dev.yml` (add `STORAGE_BASE_URL`, `STORAGE_API_KEY` env to the superset app service)

**Интерфейсы:**
- Использует: `uploader.blueprint:uploader_bp` (Задача 10), the storage stack (Задача 9).
- Предоставляет: a running Superset where `GET /uploader/` serves the page and `/uploader/api/*` proxies to the storage service. This is the final integration; verified manually.

- [ ] **Шаг 1: Добавить регистрацию блупринта в superset_config.py**

Add at the end of `superset_tech/docker/pythonpath_dev/superset_config.py`:

```python
# --- Universal File Storage uploader blueprint ---
try:
    from uploader.blueprint import uploader_bp

    BLUEPRINTS = [uploader_bp]
except Exception as _e:  # keep Superset booting even if blueprint import fails
    import logging

    logging.getLogger(__name__).warning("uploader blueprint not loaded: %s", _e)
```

- [ ] **Шаг 2: Добавить env-переменные хранилища в сервис superset в compose**

In `superset_tech/docker-compose-non-dev.yml`, locate the main `superset` app service and add under its `environment:` (or via the shared env block) :

```yaml
      STORAGE_BASE_URL: http://file-storage:8000
      STORAGE_API_KEY: change-me-dev-key
```

Ensure the storage stack shares a network with Superset: simplest is to run the storage compose (Задача 9) and add to Superset compose an external network, OR add the three storage services into the Superset compose file. For MVP, document running both composes on a shared user-defined network:

```bash
docker network create superset-shared 2>/dev/null || true
```
Add `networks: [superset-shared]` to the `file-storage` service (Задача 9 file) and to the Superset app service, plus a top-level:
```yaml
networks:
  superset-shared:
    external: true
```

- [ ] **Шаг 3: Поднять всё и проверить страницу**

Запуск:
```bash
docker network create superset-shared 2>/dev/null || true
cd services/file-storage && docker compose -f docker-compose.storage.yml up -d --build
cd ../../superset_tech && docker compose -f docker-compose-non-dev.yml up -d
sleep 20
curl -s -o /dev/null -w "%{http_code}\n" localhost:8088/uploader/
```
Ожидается: `200` (after Superset login redirect handling — if it returns `302`, open in a logged-in browser session).

- [ ] **Шаг 4: Ручная end-to-end проверка**

In a browser logged into Superset, open `http://localhost:8088/uploader/`, upload an `.svg`, confirm it appears in the list, delete it, confirm it disappears. Confirm via storage Swagger `http://localhost:8000/docs` (with API key) that the object lifecycle matches.

Ожидается: upload/list/delete all work through the Superset proxy; the API key is never present in browser network requests (check DevTools → the request to `/uploader/api/...` has no `X-API-Key`).

- [ ] **Шаг 5: Коммит**

```bash
cd /Users/romanzukov/Desktop/SUPERSET_TECH/superset_tech
git add docker/pythonpath_dev/superset_config.py docker-compose-non-dev.yml
git commit -m "feat(uploader): register uploader blueprint and wire storage into compose"
```

---

## Self-Review (самопроверка)

**Покрытие спеки:**
- Отдельное хранилище + Swagger → Задачи 1,7,8,9 (авто-`/docs` у FastAPI). ✓
- Аутентификация по API-ключу → Задача 2. ✓
- Единая таблица `files`, категория+метаданные, виртуальные папки → Задачи 3,4. ✓
- Организация по папкам → Задачи 4,6 (колонка `folder`, `list_folders`). ✓
- Полный CRUD (create/list/get/patch/replace/delete) → Задачи 6,7. ✓
- Потоковая загрузка + компенсация → Задачи 5,6. ✓
- Конфиг валидации категорий → Задача 4. ✓
- Страница Superset `/uploader/` + серверный прокси с инжектом ключа → Задачи 10,11. ✓
- Тот же Postgres, что читает Superset → Задачи 9,11 (БД `superset`). ✓
- Локальный docker-compose для всего → Задачи 9,11. ✓
- Переиспользуемость (нет импортов superset в хранилище) → закреплено в Глобальных ограничениях; код хранилища никогда не импортирует superset. ✓

**Сканирование плейсхолдеров:** Нет TBD/TODO; в каждом шаге с кодом есть полный код; у команд указан ожидаемый вывод. ✓

**Согласованность типов:** ключи `FileRecord.to_dict()` совпадают с полями `FileOut`; `metadata_`↔колонка `metadata`↔`to_dict()["metadata"]` согласованы; сигнатуры `build_object_key`/`normalize_folder` стабильны между Задачами 4/6; `get_service` определён в Задаче 7 и переопределяется в тестах. ✓

**Осознанные отступления (задокументированы, не пробелы):** нативные `JSONB`/`ARRAY` + GIN-индексы из §3 спеки отложены за портируемыми типами `JSON`/`TagList` (отмечено в Задаче 3), чтобы модели тестировались на SQLite; будущая миграция сменит типы без изменения `to_dict`/API. Аутентификация хранилища — только API-ключ (по спеке JWT не входит в цели).
