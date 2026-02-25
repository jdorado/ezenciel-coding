"""API tests for job submission and project registration.
Last edited: 2026-02-25 (job target_branch resolved from project registration)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import app, _SYSTEM_INSTRUCTIONS_QA_HEADER, _build_registered_system_instructions
from src.config import _resolve_dir, settings
from src.database.repository import _build_repository
from src.database.session import SessionLocal, engine
from src.models.job import Job
from src.models.job import Base


client = TestClient(app)
_ORIGINAL_MONGODB_URI = settings.mongodb_uri


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key or ""}


def _project_path(project_id: str) -> Path:
    return Path(_resolve_dir(settings.projects_dir)) / project_id


def setup_module(module):
    settings.mongodb_uri = None
    Base.metadata.create_all(bind=engine)
    _build_repository.cache_clear()

    for project_id in ("dummy", "dummy-dev", "project-api", "project-api-default-system"):
        path = _project_path(project_id)
        if path.exists():
            shutil.rmtree(path)

    path = _project_path("dummy")
    path.mkdir(parents=True, exist_ok=True)
    with (path / "config.yaml").open("w", encoding="utf-8") as handle:
        handle.write(
            'repository_url: "dummy"\n'
            'target_branch: "main"\n'
            'cli_client: "codex"\n'
        )

    dev_path = _project_path("dummy-dev")
    dev_path.mkdir(parents=True, exist_ok=True)
    with (dev_path / "config.yaml").open("w", encoding="utf-8") as handle:
        handle.write(
            'repository_url: "dummy-dev"\n'
            'target_branch: "dev"\n'
            'cli_client: "codex"\n'
        )


def teardown_module(module):
    for project_id in ("dummy", "dummy-dev", "project-api", "project-api-default-system"):
        path = _project_path(project_id)
        if path.exists():
            shutil.rmtree(path)

    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()

    settings.mongodb_uri = _ORIGINAL_MONGODB_URI
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
    assert data["target_branch"] == "main"
    assert "callback_url" not in data

    job_id = data["id"]
    get_response = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["id"] == job_id


def test_submit_job_rejects_legacy_callback_url_field() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "project_id": "dummy",
            "prd_content": "Legacy callback field should be rejected",
            "callback_url": "https://legacy.example/callback",
        },
        headers=_headers(),
    )
    assert response.status_code == 422


def test_submit_job_rejects_target_branch_override_field() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "project_id": "dummy",
            "prd_content": "Target branch must come from project registration.",
            "target_branch": "dev",
        },
        headers=_headers(),
    )
    assert response.status_code == 422


def test_submit_job_uses_project_target_branch() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"project_id": "dummy-dev", "prd_content": "Use project-level branch."},
        headers=_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["target_branch"] == "dev"


def test_register_project_success() -> None:
    payload = {
        "project_id": "project-api",
        "repository_url": "https://github.com/example/project-api.git",
        "target_branch": "main",
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
    assert data["target_branch"] == payload["target_branch"]
    assert payload["system_instructions"] in data["system_instructions"]
    assert _SYSTEM_INSTRUCTIONS_QA_HEADER in data["system_instructions"]
    assert "env_vars" not in data
    assert "callback_url" not in data

    project_path = _project_path("project-api")
    assert (project_path / "config.yaml").exists()
    assert (project_path / ".env").exists()
    assert (project_path / "system.md").exists()
    persisted_system = (project_path / "system.md").read_text(encoding="utf-8")
    assert payload["system_instructions"] in persisted_system
    assert _SYSTEM_INSTRUCTIONS_QA_HEADER in persisted_system


def test_register_project_injects_default_system_instructions_when_missing() -> None:
    payload = {
        "project_id": "project-api-default-system",
        "repository_url": "https://github.com/example/project-api-default-system.git",
        "target_branch": "main",
        "cli_client": "codex",
        "env_vars": {},
    }

    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 201
    data = response.json()
    assert _SYSTEM_INSTRUCTIONS_QA_HEADER in data["system_instructions"]

    project_path = _project_path("project-api-default-system")
    persisted_system = (project_path / "system.md").read_text(encoding="utf-8")
    assert _SYSTEM_INSTRUCTIONS_QA_HEADER in persisted_system


def test_build_registered_system_instructions_avoids_duplicate_contract() -> None:
    existing = f"Use strict test workflow.\n\n{_SYSTEM_INSTRUCTIONS_QA_HEADER}\nAlready present."
    merged = _build_registered_system_instructions(existing)
    assert merged.count(_SYSTEM_INSTRUCTIONS_QA_HEADER) == 1


def test_register_project_rejects_legacy_callback_fields() -> None:
    payload = {
        "project_id": "project-callback-legacy",
        "repository_url": "https://github.com/example/project-callback-legacy.git",
        "target_branch": "main",
        "cli_client": "codex",
        "callback_url": "https://legacy.example/callback",
        "callback_secret": "legacy-secret",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 422


def test_register_project_duplicate_conflict() -> None:
    payload = {
        "project_id": "project-api",
        "repository_url": "https://github.com/example/project-api.git",
        "target_branch": "main",
        "cli_client": "codex",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 409


def test_register_project_validation() -> None:
    payload = {
        "project_id": "Bad ID",
        "repository_url": "https://github.com/example/project-api.git",
        "target_branch": "main",
        "cli_client": "codex",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 422
