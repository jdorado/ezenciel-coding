"""ezenciel-coding API entrypoint.
Last edited: 2026-02-25 (enforce QA system instructions on project registration)
"""
import os
from pathlib import Path
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
import uuid

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import _resolve_dir, load_project_configs, settings
from src.database.mongo import ensure_indexes, get_mongo_db
from src.database.repository import JobRepository, get_repository
from src.database.session import engine
from src.models.job import Base
from src.worker.engine import worker_engine

app = FastAPI(title="Worker Node", version="0.1.0")


@app.on_event("startup")
def startup_event() -> None:
    if settings.mongodb_uri:
        ensure_indexes()
    else:
        Base.metadata.create_all(bind=engine)
    worker_engine.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    worker_engine.stop()


api_key_header = APIKeyHeader(name="X-API-Key")


def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key


class JobSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    prd_content: str
    target_branch: str = "main"
    env_vars_override: Optional[Dict[str, Any]] = None


class JobResponse(BaseModel):
    id: str
    project_id: str
    status: str
    phase: Optional[str]
    worker_id: Optional[str]
    logs: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    target_branch: str
    branch_name: Optional[str]
    retry_count: int
    retry_after: Optional[datetime]
    result: Optional[Dict[str, Any]]


class ProjectRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    repository_url: str
    cli_client: Literal["codex", "gemini", "claude"]
    cli_model: Optional[str] = None
    cli_effort: Optional[str] = None
    cli_flags: Optional[str] = None
    system_instructions: Optional[str] = None
    env_vars: Dict[str, str] = Field(default_factory=dict)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        if not re.fullmatch(r"^[a-z0-9_-]+$", value):
            raise ValueError("project_id must match ^[a-z0-9_-]+$")
        return value

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("repository_url must be non-empty")
        return value.strip()


class ProjectResponse(BaseModel):
    project_id: str
    repository_url: str
    cli_client: Literal["codex", "gemini", "claude"]
    cli_model: Optional[str] = None
    cli_effort: Optional[str] = None
    cli_flags: Optional[str] = None
    system_instructions: Optional[str] = None


_TERMINAL_STATUSES = {"success", "failed", "blocked", "cancelled"}
_SYSTEM_INSTRUCTIONS_QA_HEADER = "## Mandatory QA Evidence Contract"
_SYSTEM_INSTRUCTIONS_QA_CONTRACT = f"""{_SYSTEM_INSTRUCTIONS_QA_HEADER}

Treat PRD acceptance criteria as executable validation requirements, not prose.

1. Reproduce first:
- Before fixing, run a realistic runtime check that exercises the behavior through production-facing entry points.
- Prefer scripts such as `scripts/run_agent.py` or `scripts/run_tool.py` when those exist.

2. Validate after fix:
- Re-run the same realistic runtime check after code changes.
- Run focused automated tests for touched behavior.

3. Evidence is required:
- In the final report, include a `QA Evidence` section with exact commands and whether each passed or failed.
- If runtime artifacts/log files are produced, include their paths.

4. No fake confidence:
- Do not claim "fixed" without at least one real runtime verification command and one automated test command.
- If required runtime verification cannot run (missing credentials/dependencies), write `.worker_result.json` with `type=blocked` and the missing prerequisite.
"""


def _build_registered_system_instructions(system_instructions: Optional[str]) -> str:
    existing = (system_instructions or "").strip()
    if _SYSTEM_INSTRUCTIONS_QA_HEADER in existing:
        return existing
    if existing:
        return f"{existing}\n\n{_SYSTEM_INSTRUCTIONS_QA_CONTRACT}".strip()
    return _SYSTEM_INSTRUCTIONS_QA_CONTRACT.strip()


def _build_job_submit_payload(request: JobSubmitRequest) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "project_id": request.project_id,
        "prd_content": request.prd_content,
        "target_branch": request.target_branch,
        "env_vars_override": request.env_vars_override,
        "status": "queued",
    }


def _build_project_config_payload(request: ProjectRegisterRequest) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "repository_url": request.repository_url,
        "cli_client": request.cli_client,
        "system_instructions": _build_registered_system_instructions(request.system_instructions),
    }
    if request.cli_model is not None:
        payload["cli_model"] = request.cli_model
    if request.cli_effort is not None:
        payload["cli_effort"] = request.cli_effort
    if request.cli_flags is not None:
        payload["cli_flags"] = request.cli_flags
    return payload


def _build_project_response(project_id: str, payload: Dict[str, Any]) -> ProjectResponse:
    return ProjectResponse(
        project_id=project_id,
        repository_url=payload["repository_url"],
        cli_client=payload["cli_client"],
        cli_model=payload.get("cli_model"),
        cli_effort=payload.get("cli_effort"),
        cli_flags=payload.get("cli_flags"),
        system_instructions=payload.get("system_instructions"),
    )


def _write_env_file(path: Path, env_vars: Dict[str, str]) -> None:
    if not env_vars:
        return
    with path.open("w", encoding="utf-8") as handle:
        for key, value in env_vars.items():
            escaped_value = str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
            handle.write(f"{key}=\"{escaped_value}\"\n")


def _store_project_sqlite(request: ProjectRegisterRequest) -> ProjectResponse:
    projects_dir = Path(_resolve_dir(settings.projects_dir))
    projects_dir.mkdir(parents=True, exist_ok=True)

    project_path = projects_dir / request.project_id
    if project_path.exists():
        raise HTTPException(status_code=409, detail=f"Project '{request.project_id}' already registered")

    project_path.mkdir(parents=True, exist_ok=False)
    payload = _build_project_config_payload(request)

    with (project_path / "config.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)

    _write_env_file(project_path / ".env", request.env_vars)

    persisted_instructions = payload.get("system_instructions")
    if isinstance(persisted_instructions, str) and persisted_instructions.strip():
        with (project_path / "system.md").open("w", encoding="utf-8") as handle:
            handle.write(persisted_instructions.strip() + "\n")

    return _build_project_response(request.project_id, payload)


def _store_project_mongo(request: ProjectRegisterRequest) -> ProjectResponse:
    db = get_mongo_db()
    payload = _build_project_config_payload(request)

    document = {
        "_id": request.project_id,
        "project_id": request.project_id,
        **payload,
        "env_vars": dict(request.env_vars),
        "created_at": datetime.utcnow(),
    }

    try:
        db["projects"].insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=f"Project '{request.project_id}' already registered")

    return _build_project_response(request.project_id, payload)


@app.post("/api/v1/jobs", response_model=JobResponse)
def submit_job(
    request: JobSubmitRequest,
    repo: JobRepository = Depends(get_repository),
    api_key: str = Depends(verify_api_key),
) -> Dict[str, Any]:
    _ = api_key
    if not request.prd_content.strip():
        raise HTTPException(status_code=400, detail="prd_content must not be empty.")

    projects = load_project_configs()
    project = projects.get(request.project_id)
    if not project:
        raise HTTPException(status_code=400, detail=f"Project '{request.project_id}' not registered.")
    if not project.get("repository_url"):
        raise HTTPException(status_code=400, detail=f"Project '{request.project_id}' has no repository_url configured.")

    payload = _build_job_submit_payload(request)
    return repo.create(payload)


@app.get("/api/v1/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: queued, in_progress, success, failed, blocked, cancelled"),
    limit: int = Query(50, ge=1, le=500),
    repo: JobRepository = Depends(get_repository),
    api_key: str = Depends(verify_api_key),
) -> List[Dict[str, Any]]:
    _ = api_key
    return repo.list(status=status, limit=limit)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    repo: JobRepository = Depends(get_repository),
    api_key: str = Depends(verify_api_key),
) -> Dict[str, Any]:
    _ = api_key
    job = repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: str,
    repo: JobRepository = Depends(get_repository),
    api_key: str = Depends(verify_api_key),
) -> Dict[str, Any]:
    _ = api_key
    job = repo.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in _TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"Job is already in terminal state: {job['status']}")

    updated = repo.update(
        job_id,
        {
            "status": "cancelled",
            "completed_at": datetime.utcnow(),
        },
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return updated


@app.post("/api/v1/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def register_project(
    request: ProjectRegisterRequest,
    api_key: str = Depends(verify_api_key),
) -> ProjectResponse:
    _ = api_key
    if settings.mongodb_uri:
        return _store_project_mongo(request)
    return _store_project_sqlite(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5100")),
        reload=True,
        reload_dirs=["src"],
    )
