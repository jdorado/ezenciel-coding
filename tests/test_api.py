from fastapi.testclient import TestClient
from src.api.main import app
from src.database.session import SessionLocal, Base, engine
from src.models.job import Job
import os
import shutil

client = TestClient(app)

Base.metadata.create_all(bind=engine)

def setup_module(module):
    # Setup dummy project mapping for testing
    os.makedirs("projects/dummy", exist_ok=True)
    with open("projects/dummy/config.yaml", "w") as f:
        f.write('repository_url: "dummy"\ntest_command: "echo test pass"\ninstall_command: "echo install pass"\n')
    with open("projects/dummy/system.md", "w") as f:
        f.write("## Worker Instructions\n\nDefault worker instructions placeholder for test project.\n")
        
def teardown_module(module):
    if os.path.exists("projects/dummy"):
        shutil.rmtree("projects/dummy")
    
    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()

def test_submit_job_unauthorized():
    response = client.post("/api/v1/jobs", json={"project_id": "dummy", "prd_content": "hello"})
    assert response.status_code == 403

def test_submit_job_unregistered_project():
    response = client.post(
        "/api/v1/jobs", 
        json={"project_id": "unregistered", "prd_content": "hello"},
        headers={"X-API-Key": "your_secret_api_key_here"}
    )
    assert response.status_code == 400

def test_submit_job_success():
    response = client.post(
        "/api/v1/jobs", 
        json={"project_id": "dummy", "prd_content": "Implement a cool new feature"},
        headers={"X-API-Key": "your_secret_api_key_here"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["status"] == "queued"
    assert data["project_id"] == "dummy"

    # Test get job
    job_id = data["id"]
    get_response = client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "your_secret_api_key_here"})
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["id"] == job_id
