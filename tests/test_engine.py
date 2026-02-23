import pytest
from src.worker.engine import WorkerEngine
from src.database.session import SessionLocal, Base, engine
from src.models.job import Job
import os
import shutil

Base.metadata.create_all(bind=engine)

@pytest.fixture(autouse=True)
def setup_teardown():
    # Setup dummy project mapping for testing
    os.makedirs("projects/test_dummy", exist_ok=True)
    with open("projects/test_dummy/config.yaml", "w") as f:
        f.write('repository_url: "dummy"\n')
    with open("projects/test_dummy/.env", "w") as f:
        f.write('DUMMY_SECRET="hello_world"\n')
    with open("projects/test_dummy/system.md", "w") as f:
        f.write("## Worker Instructions\n\nTest-only system instructions for the engine fixture.\n")
        
    yield
    
    # Cleanup dummy project mapping and DB
    if os.path.exists("projects/test_dummy"):
        shutil.rmtree("projects/test_dummy")
        
    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()
    
    if os.path.exists("workspaces/test_dummy"):
        shutil.rmtree("workspaces/test_dummy")

def test_engine_process_jobs_skips_if_none():
    engine = WorkerEngine()
    # Shouldn"t fail if zero jobs
    engine._process_next_job()

def test_engine_process_job_mock_execution(mocker):
    # Mock subprocess to avoid real git clones during unit test
    mocker.patch("subprocess.run")
    
    db = SessionLocal()
    job = Job(id="cmd-1", project_id="test_dummy", prd_content="Hello PRD", status="queued")
    db.add(job)
    db.commit()
    db.close()
    
    worker = WorkerEngine()
    worker._process_next_job()
    
    db = SessionLocal()
    updated_job = db.query(Job).filter_by(id="cmd-1").first()
    assert updated_job.status == "success"
    # Ensure prd is saved
    assert os.path.exists("workspaces/test_dummy/PRD.md")
    
    db.close()
