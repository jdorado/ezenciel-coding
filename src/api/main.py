"""ezenciel-coding API entrypoint.
Last edited: 2026-02-23
"""
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, Depends, HTTPException, Security, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
import uuid
from datetime import datetime

from src.database.session import get_db, engine
from src.models.job import Base, Job
from src.config import settings, load_project_configs
from src.worker.engine import worker_engine

# Initialize DB schema
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Worker Node", version="0.1.0")

@app.on_event("startup")
def startup_event():
    worker_engine.start()

@app.on_event("shutdown")
def shutdown_event():
    worker_engine.stop()

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

class JobSubmitRequest(BaseModel):
    project_id: str
    prd_content: str
    target_branch: str = "main"
    callback_url: Optional[str] = None
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
    callback_url: Optional[str]
    target_branch: str
    branch_name: Optional[str]
    retry_count: int
    retry_after: Optional[datetime]
    result: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True

_TERMINAL_STATUSES = {"success", "failed", "blocked", "cancelled"}

@app.post("/api/v1/jobs", response_model=JobResponse)
def submit_job(
    request: JobSubmitRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    if not request.prd_content.strip():
        raise HTTPException(status_code=400, detail="prd_content must not be empty.")

    projects = load_project_configs()
    project = projects.get(request.project_id)
    if not project:
        raise HTTPException(status_code=400, detail=f"Project '{request.project_id}' not registered.")
    if not project.get("repository_url"):
        raise HTTPException(status_code=400, detail=f"Project '{request.project_id}' has no repository_url configured.")

    job_id = str(uuid.uuid4())
    new_job = Job(
        id=job_id,
        project_id=request.project_id,
        prd_content=request.prd_content,
        target_branch=request.target_branch,
        callback_url=request.callback_url,
        env_vars_override=request.env_vars_override,
        status="queued"
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    return new_job

@app.get("/api/v1/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: queued, in_progress, success, failed, blocked, cancelled"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status)
    jobs = query.order_by(Job.created_at.desc()).limit(limit).all()
    return jobs

@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"Job is already in terminal state: {job.status}")
    job.status = "cancelled"
    job.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5100")),
        reload=True,
        reload_dirs=["src"],
    )
