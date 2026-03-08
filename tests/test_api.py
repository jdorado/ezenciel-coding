"""API tests for job submission and project registration.
Last edited: 2026-02-27 (cover generic pre-job setup registration fields)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import mongomock
from fastapi.testclient import TestClient

from src.api.main import app, _SYSTEM_INSTRUCTIONS_QA_HEADER, _build_registered_system_instructions
from src.config import _resolve_dir, settings
from src.database import mongo as mongo_module
from src.database.mongo import get_mongo_db
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

    for project_id in ("dummy", "dummy-dev", "dummy-no-branch", "project-api", "project-api-default-system"):
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

    no_branch_path = _project_path("dummy-no-branch")
    no_branch_path.mkdir(parents=True, exist_ok=True)
    with (no_branch_path / "config.yaml").open("w", encoding="utf-8") as handle:
        handle.write(
            'repository_url: "dummy-no-branch"\n'
            'cli_client: "codex"\n'
        )


def teardown_module(module):
    for project_id in ("dummy", "dummy-dev", "dummy-no-branch", "project-api", "project-api-default-system"):
        path = _project_path(project_id)
        if path.exists():
            shutil.rmtree(path)

    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()

    settings.mongodb_uri = _ORIGINAL_MONGODB_URI
    get_mongo_db.cache_clear()
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


def test_submit_job_defaults_target_branch_to_main_when_project_config_missing() -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"project_id": "dummy-no-branch", "prd_content": "Use default branch when missing in config."},
        headers=_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["target_branch"] == "main"


def test_register_project_success() -> None:
    payload = {
        "project_id": "project-api",
        "repository_url": "https://github.com/example/project-api.git",
        "target_branch": "main",
        "cli_client": "claude",
        "cli_model": "claude-opus-4-6",
        "system_instructions": "Use strict test-first workflow.",
        "pre_job_setup_command": "poetry install --no-root --no-ansi",
        "pre_job_setup_commands": ["poetry run baml-cli generate --from baml_src"],
        "pre_job_setup_timeout_seconds": 1200,
        "pr_reviewer_login": "test",
        "pr_reviewer_email": "reviewer@example.com",
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
    assert data["pre_job_setup_command"] == payload["pre_job_setup_command"]
    assert data["pre_job_setup_commands"] == payload["pre_job_setup_commands"]
    assert data["pre_job_setup_timeout_seconds"] == payload["pre_job_setup_timeout_seconds"]
    assert data["pr_reviewer_login"] == payload["pr_reviewer_login"]
    assert data["pr_reviewer_email"] == payload["pr_reviewer_email"]
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


def test_register_project_validation_rejects_invalid_reviewer_email() -> None:
    payload = {
        "project_id": "project-api-invalid-reviewer",
        "repository_url": "https://github.com/example/project-api-invalid-reviewer.git",
        "target_branch": "main",
        "cli_client": "codex",
        "pr_reviewer_email": "not-an-email",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 422


def test_register_project_validation_rejects_invalid_reviewer_login() -> None:
    payload = {
        "project_id": "project-api-invalid-reviewer-login",
        "repository_url": "https://github.com/example/project-api-invalid-reviewer-login.git",
        "target_branch": "main",
        "cli_client": "codex",
        "pr_reviewer_login": "invalid login",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 422


def test_register_project_mongo_persists_reviewer_email(monkeypatch) -> None:
    fake_client = mongomock.MongoClient()
    monkeypatch.setattr(mongo_module, "MongoClient", lambda *args, **kwargs: fake_client)
    get_mongo_db.cache_clear()
    original_uri = settings.mongodb_uri
    settings.mongodb_uri = "mongodb://localhost:27017/test_projects"
    _build_repository.cache_clear()

    payload = {
        "project_id": "project-api-mongo",
        "repository_url": "https://github.com/example/project-api-mongo.git",
        "target_branch": "main",
        "cli_client": "codex",
        "pr_reviewer_login": "octocat",
        "pr_reviewer_email": "mongo-reviewer@example.com",
    }
    response = client.post("/api/v1/projects", json=payload, headers=_headers())
    assert response.status_code == 201
    data = response.json()
    assert data["pr_reviewer_login"] == payload["pr_reviewer_login"]
    assert data["pr_reviewer_email"] == payload["pr_reviewer_email"]

    db = get_mongo_db()
    doc = db["projects"].find_one({"project_id": payload["project_id"]})
    assert doc is not None
    assert doc["pr_reviewer_login"] == payload["pr_reviewer_login"]
    assert doc["pr_reviewer_email"] == payload["pr_reviewer_email"]

    settings.mongodb_uri = original_uri
    get_mongo_db.cache_clear()
    _build_repository.cache_clear()
