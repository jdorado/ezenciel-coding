"""Repository tests for SQLite and Mongo implementations.
Last edited: 2026-02-25 (cover claim/update/list behavior across both backends)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import mongomock
import pytest

from src.config import settings
from src.database import mongo as mongo_module
from src.database.mongo import ensure_indexes, get_mongo_db
from src.database.repository import MongoJobRepository, SQLiteJobRepository
from src.database.session import SessionLocal, engine
from src.models.job import Base, Job


@pytest.fixture(autouse=True)
def clean_sqlite_jobs():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()


def test_sqlite_repository_claim_and_update_flow() -> None:
    repo = SQLiteJobRepository()
    job = repo.create({"project_id": "repo-a", "prd_content": "hello", "target_branch": "main"})

    claimed = repo.claim_next("worker-1")
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "in_progress"
    assert claimed["worker_id"] == "worker-1"

    updated = repo.update(job["id"], {"status": "success", "phase": "done"})
    assert updated is not None
    assert updated["status"] == "success"
    assert updated["phase"] == "done"


def test_sqlite_repository_list_stale_and_retryable() -> None:
    repo = SQLiteJobRepository()
    stale_started = datetime.utcnow() - timedelta(minutes=30)
    retry_after = datetime.utcnow() - timedelta(minutes=5)

    in_progress = repo.create({
        "project_id": "repo-a",
        "prd_content": "stale",
        "status": "in_progress",
        "started_at": stale_started,
    })
    retryable = repo.create({
        "project_id": "repo-a",
        "prd_content": "retry",
        "status": "queued",
        "retry_after": retry_after,
    })

    stale = repo.list_stale(datetime.utcnow() - timedelta(minutes=10))
    retryable_rows = repo.list_retryable(datetime.utcnow())

    assert any(row["id"] == in_progress["id"] for row in stale)
    assert any(row["id"] == retryable["id"] for row in retryable_rows)


def test_mongo_repository_claim_and_update_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = mongomock.MongoClient()
    monkeypatch.setattr(mongo_module, "MongoClient", lambda *args, **kwargs: fake_client)
    get_mongo_db.cache_clear()

    original_uri = settings.mongodb_uri
    settings.mongodb_uri = "mongodb://localhost:27017/test_repo"

    ensure_indexes()
    repo = MongoJobRepository()
    job = repo.create({"project_id": "repo-b", "prd_content": "hello", "target_branch": "main"})

    claimed = repo.claim_next("worker-2")
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "in_progress"

    updated = repo.update(job["id"], {"status": "success", "phase": "done"})
    assert updated is not None
    assert updated["status"] == "success"

    settings.mongodb_uri = original_uri
    get_mongo_db.cache_clear()


def test_mongo_repository_list_stale_and_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = mongomock.MongoClient()
    monkeypatch.setattr(mongo_module, "MongoClient", lambda *args, **kwargs: fake_client)
    get_mongo_db.cache_clear()

    original_uri = settings.mongodb_uri
    settings.mongodb_uri = "mongodb://localhost:27017/test_repo"

    repo = MongoJobRepository()
    stale_started = datetime.utcnow() - timedelta(minutes=30)
    retry_after = datetime.utcnow() - timedelta(minutes=5)

    in_progress = repo.create({
        "project_id": "repo-b",
        "prd_content": "stale",
        "status": "in_progress",
        "started_at": stale_started,
    })
    retryable = repo.create({
        "project_id": "repo-b",
        "prd_content": "retry",
        "status": "queued",
        "retry_after": retry_after,
    })

    stale = repo.list_stale(datetime.utcnow() - timedelta(minutes=10))
    retryable_rows = repo.list_retryable(datetime.utcnow())

    assert any(row["id"] == in_progress["id"] for row in stale)
    assert any(row["id"] == retryable["id"] for row in retryable_rows)

    settings.mongodb_uri = original_uri
    get_mongo_db.cache_clear()
