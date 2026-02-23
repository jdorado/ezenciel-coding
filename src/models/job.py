"""Job SQLAlchemy model.
Last edited: 2026-02-23
"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True)
    project_id = Column(String, index=True)
    prd_content = Column(Text, nullable=False)
    status = Column(String, default="queued", index=True) # queued, in_progress, success, failed, blocked, cancelled
    phase = Column(String, nullable=True)                  # claimed, syncing, executing, committing, pushing, creating_pr
    worker_id = Column(String, nullable=True)              # Hostname of the worker that claimed this job
    logs = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    callback_url = Column(String, nullable=True)
    env_vars_override = Column(JSON, nullable=True) # Optional JSON dict for job-specific env overrides
    target_branch = Column(String, default="main", nullable=False)
    branch_name = Column(String, nullable=True)   # Job branch created by worker (worker/{job_id[:8]})
    retry_count = Column(Integer, default=0)
    retry_after = Column(DateTime, nullable=True) # When to retry rate-limited jobs
    result = Column(JSON, nullable=True)           # Structured outcome: {type, summary, pr_url, branch, diffstat, commands_ran, ...}
