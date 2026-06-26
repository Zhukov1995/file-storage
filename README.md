# File Storage Service

Универсальный сервис хранения файлов: загрузка, хранение и управление любыми
файлами (IFC/BIM-модели, изображения, SVG и т.д.) через REST API. Файлы лежат
в объектном хранилище (MinIO/S3), метаданные — в Postgres. Сервис самостоятельный
и переиспользуемый — он ничего не знает о Superset и подключается к любому проекту
по HTTP.

- **REST API + Swagger** — `http://localhost:8000/docs`
- **Веб-консоль** (загрузка/правка/удаление, тёмная/светлая тема) — `http://localhost:8000/`
- **Хранилище** — MinIO (S3-совместимое), консоль `http://localhost:9001`

---

## Возможности

- Единый реестр `files`: у каждого файла есть `category`, виртуальная `folder`,
  `tags` и произвольные `metadata` (JSON). Тип файла — это категория, а не
  отдельная таблица. Добавить новый тип = одна строка в конфиге.
- Полный CRUD: загрузка, список с фильтрами, получение (со ссылкой на скачивание),
  изменение метаданных, замена файла, удаление.
- Потоковая загрузка в хранилище (файл не буферизуется целиком в память).
- Аутентификация по API-ключу (`X-API-Key`).
- Защита: санитизация путей и имён файлов, проверка категории/расширения/размера
  до загрузки, constant-time сравнение ключа, компенсация при сбоях (без «осиротевших» файлов).

---

## Требования

- Docker + Docker Compose v2
- (для локальной разработки без Docker) Python 3.9

---

## Быстрый старт (Docker)

Из каталога `services/file-storage/`:

```bash
# 1. подготовить конфиг
cp -n .env.example .env

# 2. поднять весь стек (file-storage + MinIO + Postgres)
docker compose -f docker-compose.storage.yml up -d --build

# 3. проверить, что сервис живой
curl localhost:8000/healthz        # -> {"status":"ok"}
```

Готово. Открывай:

| Что | URL | Доступ |
|-----|-----|--------|
| Веб-консоль | http://localhost:8000/ | API-ключ вводится на странице |
| Swagger (API) | http://localhost:8000/docs | заголовок `X-API-Key` |
| MinIO-консоль | http://localhost:9001 | `minioadmin` / `minioadmin` |

**API-ключ по умолчанию:** `change-me-dev-key` (поменяй в `.env`, переменная `API_KEY`).

Остановить (данные сохраняются на Docker volume):
```bash
docker compose -f docker-compose.storage.yml down
```
Удалить вместе с данными:
```bash
docker compose -f docker-compose.storage.yml down -v
```

---

## Как пользоваться: веб-консоль

Открой http://localhost:8000/ и введи API-ключ (по умолчанию подставлен `change-me-dev-key`).

- **Загрузка** — имя, категория, папка (например `/проект-1`), теги, файл → «Загрузить».
- **Список** — таблица всех файлов, фильтр по категории, счётчик.
- **Изменить** — инлайн-форма: имя / категория / папка / теги.
- **Заменить** — загрузить новый файл вместо текущего (метаданные сохраняются).
- **Удалить** — с подтверждением (удаляет и запись, и сам файл из хранилища).
- **Тема** — переключатель ☀ Светлая / ◐ Система / ☾ Тёмная (выбор запоминается).

---

## Как пользоваться: REST API

Все эндпоинты, кроме `GET /` и `GET /healthz`, требуют заголовок `X-API-Key`.
Интерактивно всё доступно в Swagger: http://localhost:8000/docs

### Категории и лимиты

| Категория | Расширения | Макс. размер |
|-----------|-----------|--------------|
| `bim`   | `.ifc` | 500 МБ |
| `image` | `.png`, `.jpg`, `.jpeg`, `.webp` | 50 МБ |
| `svg`   | `.svg` | 5 МБ |

Неизвестная категория / неверное расширение / превышение размера → `422`.

### Эндпоинты

| Метод | Путь | Действие |
|-------|------|----------|
| `POST`   | `/files` | Загрузить файл (multipart: `name`, `category`, `folder`, `tags`, `metadata`, `file`) |
| `GET`    | `/files` | Список. Фильтры: `?category=&folder=&prefix=&tag=&q=` + `limit`/`offset` |
| `GET`    | `/files/{id}` | Метаданные + `download_url` (presigned-ссылка) |
| `PATCH`  | `/files/{id}` | Изменить `name`/`category`/`folder`/`tags`/`metadata` (JSON-тело) |
| `PUT`    | `/files/{id}/content` | Заменить сам файл (multipart `file`) |
| `DELETE` | `/files/{id}` | Удалить запись и файл из хранилища |
| `GET`    | `/folders` | Список папок (опц. `?category=`) |
| `GET`    | `/healthz` | Проверка живости (без ключа) |

### Примеры (curl)

```bash
KEY="X-API-Key: change-me-dev-key"

# Загрузить
curl -X POST localhost:8000/files -H "$KEY" \
  -F name="Корпус А" -F category=bim -F folder=/estate-1 -F tags=корпус,новый \
  -F file=@model.ifc

# Список (только bim)
curl localhost:8000/files?category=bim -H "$KEY"

# Получить с ссылкой на скачивание
curl localhost:8000/files/1 -H "$KEY"

# Переименовать / сменить папку / теги
curl -X PATCH localhost:8000/files/1 -H "$KEY" -H "Content-Type: application/json" \
  -d '{"name":"Корпус А (ред.)","folder":"/estate-1/корпуса","tags":["готов"]}'

# Заменить файл
curl -X PUT localhost:8000/files/1/content -H "$KEY" -F file=@model_v2.ifc

# Удалить
curl -X DELETE localhost:8000/files/1 -H "$KEY"
```

---

## Конфигурация (`.env`)

| Переменная | Назначение | По умолчанию |
|-----------|-----------|--------------|
| `DATABASE_URL` | Postgres для реестра | `postgresql+psycopg2://superset:superset@postgres:5432/superset` |
| `S3_ENDPOINT` | Адрес MinIO/S3 | `http://minio:9000` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Ключи хранилища | `minioadmin` / `minioadmin` |
| `S3_BUCKET` | Бакет для файлов | `files` |
| `API_KEY` | Ключ доступа к API | `change-me-dev-key` |

> Реестр кладётся в тот же Postgres, который читает Superset (база `superset`),
> чтобы дашборды могли запрашивать таблицу `files` напрямую.

### Другое хранилище вместо MinIO

Сервис работает с любым S3-совместимым хранилищем (AWS S3, Yandex Object Storage,
SeaweedFS и т.д.) — меняется только `S3_ENDPOINT` и ключи в `.env`, код не трогается.

---

## Разработка и тесты

Из каталога `services/file-storage/`:

```bash
# окружение
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# юнит-тесты (без внешних сервисов)
.venv/bin/python -m pytest tests/ -q

# интеграционные тесты (нужен живой MinIO на localhost:9000)
S3_ENDPOINT=http://localhost:9000 S3_ACCESS_KEY=minioadmin \
S3_SECRET_KEY=minioadmin S3_BUCKET=files \
  .venv/bin/python -m pytest tests/test_storage_integration.py -q
```

Локальный запуск без Docker (нужны доступные Postgres и MinIO):
```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

---

## Структура

```
services/file-storage/
├── app/
│   ├── main.py          # FastAPI-приложение, /healthz, /, подключение роутов
│   ├── config.py        # настройки из переменных окружения
│   ├── auth.py          # проверка X-API-Key
│   ├── db.py            # сессия SQLAlchemy
│   ├── models.py        # таблица files (FileRecord)
│   ├── categories.py    # правила категорий (расширения, лимиты)
│   ├── keys.py          # нормализация папок и генерация object_key
│   ├── storage.py       # клиент объектного хранилища (boto3/S3)
│   ├── service.py       # CRUD-логика, компенсация при сбоях
│   ├── routes.py        # REST-эндпоинты
│   ├── schemas.py       # Pydantic-модели ответов/запросов
│   └── static/
│       └── index.html   # веб-консоль
├── tests/               # юнит- и интеграционные тесты
├── Dockerfile
├── docker-compose.storage.yml
├── .env.example
└── pyproject.toml
```

---

## Известные ограничения (MVP)

- Конвертация IFC→XKT и извлечение данных элементов (GlobalId) — отдельный
  следующий этап, в этот сервис пока не входят.
- `download_url` указывает на внутренний адрес хранилища (`minio:9000`) — рабочая
  ссылка внутри docker-сети; для внешнего доступа раздаётся через прокси.
- Версии зависимостей в `requirements.txt` не зафиксированы жёстко — перед
  продакшеном рекомендуется запинить.
