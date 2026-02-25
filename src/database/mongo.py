"""MongoDB helpers for ezenciel-coding.
Last edited: 2026-02-25 (add cached db client and required indexes)
"""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from src.config import settings


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    name = parsed.path.lstrip("/")
    if not name:
        return "ezenciel_coding"
    return name.split("/")[0]


@lru_cache(maxsize=1)
def get_mongo_db() -> Database:
    """Return cached Mongo database from MONGODB_URI."""
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required for MongoDB backend")

    client = MongoClient(settings.mongodb_uri)
    database_name = _database_name_from_uri(settings.mongodb_uri)
    return client[database_name]


def ensure_indexes() -> None:
    """Create required indexes for jobs and projects collections."""
    db = get_mongo_db()

    jobs = db["jobs"]
    jobs.create_index([("status", ASCENDING)])
    jobs.create_index([("project_id", ASCENDING)])
    jobs.create_index([("created_at", ASCENDING)])
    jobs.create_index([("retry_after", ASCENDING)])

    projects = db["projects"]
    projects.create_index([("project_id", ASCENDING)], unique=True)
