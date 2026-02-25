"""API tests for job submission and project registration.
Last edited: 2026-02-25 (add project registration coverage and repository-backed job flow)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import app
from src.config import _resolve_dir, settings
from src.database.repository import _build_repository
from src.database.session import SessionLocal, engine
from src.models.job import Job
from src.models.job import Base


client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key or ""}


def _project_path(project_id: str) -> Path:
    return Path(_resolve_dir(settings.projects_dir)) / project_id


def setup_module(module):
    Base.metadata.create_all(bind=engine)
    _build_repository.cache_clear()

    path = _project_path("dummy")
    path.mkdir(parents=True, exist_ok=True)
    with (path / "config.yaml").open("w", encoding="utf-8") as handle:
        handle.write('repository_url: "dummy"\ncli_client: "codex"\n')


def teardown_module(module):
    for project_id in ("dummy", "project-api"):
        path = _project_path(project_id)
        if path.exists():
            shutil.rmtree(path)

    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()

    _build_repository.cache_clear()


def test_submit_job_unauthorized() -> None:
    response = client.post("/api/v1/jobs", json={"project_id": "dummy", "prd_content": "hello"})
    assert response.status_code == 401


def test_submit_job_unregistered_project() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"project_id": "unregistered", "prd_content": "hello"},
        headers=_headers(),
    )
    assert response.status_code == 400


def test_submit_job_success() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"project_id": "dummy", "prd_content": "Implement a cool new feature"},
        headers=_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["status"] == "queued"
    assert data["project_id"] == "dummy"

    job_id = data["id"]
    get_response = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["id"] == job_id


def test_register_project_success() -> None:
    payload = {
        "project_id": "project-api",
        "repository_url": "https://github.com/example/project-api.git",
        "cli_client": "claude",
        "cli_model": "claude-opus-4-6",
        "system_instructions": "Use strict test-first workflow.",
        "env_vars": {"GITHUB_TOKEN": "token123"},
    }

    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 201
    data = response.json()
    assert data["project_id"] == "project-api"
    assert data["repository_url"] == payload["repository_url"]
    assert "env_vars" not in data

    project_path = _project_path("project-api")
    assert (project_path / "config.yaml").exists()
    assert (project_path / ".env").exists()
    assert (project_path / "system.md").exists()


def test_register_project_duplicate_conflict() -> None:
    payload = {
        "project_id": "project-api",
        "repository_url": "https://github.com/example/project-api.git",
        "cli_client": "codex",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 409


def test_register_project_validation() -> None:
    payload = {
        "project_id": "Bad ID",
        "repository_url": "https://github.com/example/project-api.git",
        "cli_client": "codex",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 422
