"""Job repository abstraction for SQLite and MongoDB backends.
Last edited: 2026-02-25 (remove callback_url persistence from job contract)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional
import uuid

from pymongo import ReturnDocument

from src.config import settings
from src.database.mongo import get_mongo_db
from src.database.session import SessionLocal
from src.models.job import Job


class JobRepository(ABC):
    @abstractmethod
    def create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def list(self, status: Optional[str], limit: int) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update(self, job_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def claim_next(self, worker_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def list_stale(self, cutoff: datetime) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def list_retryable(self, now: datetime) -> List[Dict[str, Any]]:
        pass


_SQLITE_JOB_COLUMNS = (
    "id",
    "project_id",
    "prd_content",
    "status",
    "phase",
    "worker_id",
    "logs",
    "created_at",
    "started_at",
    "completed_at",
    "env_vars_override",
    "target_branch",
    "branch_name",
    "retry_count",
    "retry_after",
    "result",
)


def _sqlite_to_dict(job: Job) -> Dict[str, Any]:
    return {column: getattr(job, column) for column in _SQLITE_JOB_COLUMNS}


class SQLiteJobRepository(JobRepository):
    def create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(fields)
        payload.setdefault("id", str(uuid.uuid4()))
        payload.setdefault("status", "queued")

        db = SessionLocal()
        try:
            model = Job(**payload)
            db.add(model)
            db.commit()
            db.refresh(model)
            return _sqlite_to_dict(model)
        finally:
            db.close()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        db = SessionLocal()
        try:
            model = db.query(Job).filter(Job.id == job_id).first()
            return _sqlite_to_dict(model) if model else None
        finally:
            db.close()

    def list(self, status: Optional[str], limit: int) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            query = db.query(Job)
            if status:
                query = query.filter(Job.status == status)
            rows = query.order_by(Job.created_at.desc()).limit(limit).all()
            return [_sqlite_to_dict(row) for row in rows]
        finally:
            db.close()

    def update(self, job_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        db = SessionLocal()
        try:
            model = db.query(Job).filter(Job.id == job_id).first()
            if not model:
                return None
            for key, value in fields.items():
                setattr(model, key, value)
            db.commit()
            db.refresh(model)
            return _sqlite_to_dict(model)
        finally:
            db.close()

    def claim_next(self, worker_id: str) -> Optional[Dict[str, Any]]:
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            model = (
                db.query(Job)
                .filter(Job.status == "queued")
                .filter((Job.retry_after == None) | (Job.retry_after <= now))
                .order_by(Job.created_at.asc())
                .first()
            )
            if not model:
                return None

            model.status = "in_progress"
            model.worker_id = worker_id
            model.started_at = now
            model.retry_after = None
            model.phase = "claimed"
            db.commit()
            db.refresh(model)
            return _sqlite_to_dict(model)
        finally:
            db.close()

    def list_stale(self, cutoff: datetime) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            rows = (
                db.query(Job)
                .filter(Job.status == "in_progress")
                .filter(Job.started_at <= cutoff)
                .all()
            )
            return [_sqlite_to_dict(row) for row in rows]
        finally:
            db.close()

    def list_retryable(self, now: datetime) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            rows = (
                db.query(Job)
                .filter(Job.status == "queued")
                .filter(Job.retry_after <= now)
                .all()
            )
            return [_sqlite_to_dict(row) for row in rows]
        finally:
            db.close()


def _mongo_to_dict(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    payload = dict(doc)
    payload.pop("_id", None)
    return payload


class MongoJobRepository(JobRepository):
    def __init__(self):
        db = get_mongo_db()
        self.jobs = db["jobs"]

    def create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.utcnow()
        payload = dict(fields)
        payload.setdefault("id", str(uuid.uuid4()))
        payload.setdefault("status", "queued")
        payload.setdefault("logs", "")
        payload.setdefault("phase", None)
        payload.setdefault("worker_id", None)
        payload.setdefault("created_at", now)
        payload.setdefault("started_at", None)
        payload.setdefault("completed_at", None)
        payload.setdefault("env_vars_override", None)
        payload.setdefault("target_branch", "main")
        payload.setdefault("branch_name", None)
        payload.setdefault("retry_count", 0)
        payload.setdefault("retry_after", None)
        payload.setdefault("result", None)
        payload["_id"] = payload["id"]

        self.jobs.insert_one(payload)
        payload.pop("_id", None)
        return payload

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        doc = self.jobs.find_one({"_id": job_id})
        return _mongo_to_dict(doc)

    def list(self, status: Optional[str], limit: int) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if status:
            query["status"] = status
        cursor = self.jobs.find(query).sort("created_at", -1).limit(limit)
        return [_mongo_to_dict(doc) for doc in cursor]

    def update(self, job_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not fields:
            return self.get(job_id)
        doc = self.jobs.find_one_and_update(
            {"_id": job_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
        return _mongo_to_dict(doc)

    def claim_next(self, worker_id: str) -> Optional[Dict[str, Any]]:
        now = datetime.utcnow()
        doc = self.jobs.find_one_and_update(
            {
                "status": "queued",
                "$or": [
                    {"retry_after": {"$exists": False}},
                    {"retry_after": None},
                    {"retry_after": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "in_progress",
                    "worker_id": worker_id,
                    "started_at": now,
                    "retry_after": None,
                    "phase": "claimed",
                }
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return _mongo_to_dict(doc)

    def list_stale(self, cutoff: datetime) -> List[Dict[str, Any]]:
        cursor = self.jobs.find(
            {
                "status": "in_progress",
                "started_at": {"$lte": cutoff},
            }
        )
        return [_mongo_to_dict(doc) for doc in cursor]

    def list_retryable(self, now: datetime) -> List[Dict[str, Any]]:
        cursor = self.jobs.find(
            {
                "status": "queued",
                "retry_after": {"$lte": now},
            }
        )
        return [_mongo_to_dict(doc) for doc in cursor]


@lru_cache(maxsize=4)
def _build_repository(mongodb_uri: Optional[str], db_path: str) -> JobRepository:
    if mongodb_uri:
        return MongoJobRepository()
    return SQLiteJobRepository()


def get_repository() -> JobRepository:
    """Repository dependency for API and worker engine."""
    return _build_repository(settings.mongodb_uri, settings.db_path)
