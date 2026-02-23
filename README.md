# ezenciel-coding

REST API + worker that runs AI coding agents (codex, gemini, claude) against your repos.

Submit a PRD → agent clones repo, implements it, commits, pushes, opens PR.

## Setup

```bash
cp .env.sample .env
# Edit .env and set API_KEY
poetry install
poetry run uvicorn src.api.main:app --port 8080 --reload
```

Or with Docker:

```bash
docker-compose up --build -d
```

## Register a project

Create a directory under `projects/` with a `config.yaml`:

```yaml
# projects/my-repo/config.yaml
repository_url: "https://github.com/my-org/my-repo.git"
cli_client: "codex"       # codex | gemini | claude
cli_model: "gpt-4o"
cli_effort: "high"
cli_flags: "--yes --force"
```

Add a `.env` with credentials the agent needs (e.g. `GITHUB_TOKEN`).

See `projects/dummy-repo/` for a full example.

## Submit a job

```bash
curl -X POST http://localhost:8080/api/v1/jobs \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-repo",
    "prd_content": "Add input validation to the login endpoint."
  }'
```

## Check status

```bash
curl http://localhost:8080/api/v1/jobs/{job_id} -H "X-API-Key: your_api_key"
```
