# MongoDB + Project Registration Endpoint
**Created:** 2026-02-25
**Last edited:** 2026-02-25
**Status:** Implemented

---

## Goal

Add MongoDB as an optional primary backend alongside SQLite. Both are full backends — one is active at a time. MongoDB brings external data visibility and editability (Compass, Atlas UI) without any dual-write complexity. The engine and API use an abstract repository layer so the backend is swappable by config.

---

## Why MongoDB

| | SQLite | MongoDB |
|---|---|---|
| Setup | Zero dependencies | Requires running instance |
| Data visibility | File only (`worker.db`) | Compass / Atlas UI, live queries |
| External edits | Not practical | Edit documents directly, override job state |
| Ops / debugging | Hard to inspect externally | Browse, filter, patch without code |
| Best for | Local dev, simple deploys | Production, shared infra, ops tooling |

One source of truth. No sync. No dual-write.

---

## Design Decisions (KISS)

1. **Single backend per deployment** — `MONGODB_URI` set → MongoDB. Unset → SQLite. No dual-write.
2. **Abstract repository layer** — engine and API call `JobRepository` methods. Never touch the DB driver directly.
3. **Sync pymongo** — existing code is synchronous; no async refactor.
4. **Project configs follow the same backend** — disk folders for SQLite, `projects` collection for MongoDB.
5. **`load_project_configs()` reads from active backend** — single code path, no branching at call sites.

---

## Architecture

```
MONGODB_URI unset  →  SQLiteJobRepository   (SQLAlchemy, existing logic)
MONGODB_URI set    →  MongoJobRepository    (pymongo, new)
                              ↕
                     JobRepository (interface)
                              ↕
         API (main.py)    Worker Engine (engine.py)
```

```
Project configs:
  SQLite mode  →  disk  projects/{id}/config.yaml
  MongoDB mode →  MongoDB  projects  collection
                              ↕
                  load_project_configs()  ← same call, backend-aware
```

---

## Repository Interface

`src/database/repository.py`

```python
class JobRepository:
    def create(self, fields: dict) -> dict: ...
    def get(self, job_id: str) -> dict | None: ...
    def list(self, status: str | None, limit: int) -> list[dict]: ...
    def update(self, job_id: str, fields: dict) -> dict | None: ...
    def claim_next(self, worker_id: str) -> dict | None: ...  # atomic
    def list_stale(self, cutoff: datetime) -> list[dict]: ...
    def list_retryable(self, now: datetime) -> list[dict]: ...
```

All methods return plain `dict`. No ORM objects leak outside the repository.

**Atomic claim:**
- SQLite: `SELECT ... WHERE status='queued' ORDER BY created_at LIMIT 1` + UPDATE in a transaction
- MongoDB: `find_one_and_update({status: 'queued'}, {$set: {status: 'in_progress', worker_id: ...}}, sort=[created_at, 1], return_document=AFTER)`

---

## Files to Create / Modify

### NEW: `src/database/repository.py`
- `JobRepository` base class
- `SQLiteJobRepository` — extracts existing SQLAlchemy logic from `engine.py` + `main.py`
- `MongoJobRepository` — pymongo implementation
- `get_repository() -> JobRepository` — factory reads `settings.mongodb_uri`

### NEW: `src/database/mongo.py`
- `get_mongo_db()` — cached singleton pymongo `Database`
- `ensure_indexes()` — called once at startup
- Collections: `jobs`, `projects`
- Indexes on `jobs`: `status`, `project_id`, `created_at`, `retry_after`
- Index on `projects`: `project_id` (unique)

### MODIFY: `src/config.py`
- Add `mongodb_uri: Optional[str] = None` to `Settings`
- Update `load_project_configs()`:
  - MongoDB mode → query `projects` collection
  - SQLite mode → existing disk folder scan (unchanged)

### MODIFY: `src/api/main.py`
- Replace `db: Session = Depends(get_db)` → `repo: JobRepository = Depends(get_repository)`
- Replace all `db.query(Job)...` calls with `repo.*` calls
- On startup: `mongo.ensure_indexes()` if MongoDB active; else `Base.metadata.create_all`
- Add `POST /api/v1/projects` endpoint

### MODIFY: `src/worker/engine.py`
- Replace `Session` with `JobRepository` (injected at engine init)
- `_process_next_job` → `repo.claim_next(worker_id)`
- All job state updates → `repo.update(job_id, {...})`
- `_recover_stale_jobs` → `repo.list_stale(cutoff)` + `repo.update`
- Engine thread: pymongo is thread-safe; SQLAlchemy session scoped per operation

### MODIFY: `pyproject.toml`
- Add `pymongo>=4.7,<5`

---

## New Endpoint: `POST /api/v1/projects`

**Auth:** `X-API-Key`

**Request:**
```json
{
  "project_id": "my-repo",
  "repository_url": "git@github.com:org/my-repo.git",
  "cli_client": "claude",
  "cli_model": "claude-opus-4-6",
  "cli_effort": null,
  "cli_flags": null,
  "system_instructions": "optional per-project agent prompt",
  "env_vars": {}
}
```

**Storage by backend:**

| | SQLite mode | MongoDB mode |
|---|---|---|
| Write | `projects/{id}/config.yaml` + `system.md` on disk | Upsert into `projects` collection |
| `env_vars` | Written to `projects/{id}/.env` (never in DB) | Stored in document (DB-level encryption caller's responsibility) |
| 409 | Folder already exists | Unique index violation on `project_id` |

**Response:** `201 Created` with project config (`env_vars` excluded from response always).

**Validation:**
- `project_id`: `^[a-z0-9_-]+$`
- `repository_url`: non-empty string
- `cli_client`: `codex | gemini | claude`

---

## Implementation Checklist

- [x] `src/database/repository.py` — interface, `SQLiteJobRepository`, `MongoJobRepository`, factory
- [x] `src/database/mongo.py` — client singleton, `ensure_indexes`
- [x] `src/config.py` — `mongodb_uri` field, update `load_project_configs()`
- [x] `src/api/main.py` — wire `get_repository`, add `POST /api/v1/projects`, startup init
- [x] `src/worker/engine.py` — replace Session with `JobRepository`
- [x] `pyproject.toml` — add `pymongo`
- [x] `tests/test_api.py` — project registration tests
- [x] `tests/test_repository.py` — both impls with fixtures

## Verification

- Ran: `poetry run python -m pytest -q tests/test_api.py tests/test_engine.py tests/test_repository.py`
- Result: `12 passed`

---

## Out of Scope

- Async Motor driver
- Dual-write / mirroring between backends
- Migration tool SQLite → MongoDB
- Project deletion endpoint

---

## Environment

```env
# SQLite (default)
DB_PATH=sqlite:///data/worker.db

# MongoDB — replaces SQLite entirely
MONGODB_URI=mongodb://localhost:27017/ezenciel_coding
```

Docker Compose (local dev):
```yaml
mongo:
  image: mongo:7
  ports: ["27017:27017"]
  volumes: ["mongo_data:/data/db"]
```
