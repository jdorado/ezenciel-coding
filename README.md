# ezenciel-coding
<!-- Updated: 2026-02-23 -->

REST API + worker that runs AI coding agents (codex, gemini, claude) against your repos.

Submit a PRD → agent clones repo, implements it, commits, pushes, opens PR.

**Supported project types:** Python/FastAPI, Next.js. Agents handle dependency installation — the container ships with Python 3.13 and Node 22.

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

## CLI Agent Credentials

The worker runs codex, gemini, or claude depending on your project config. Authenticate each agent on the **host machine** before starting the container — Docker mounts your credential directories at runtime.

### Codex
```bash
npm install -g @openai/codex
codex login
# Credentials stored in ~/.codex/
```

### Gemini
```bash
npm install -g @google/gemini-cli
gemini login
# Credentials stored in ~/.gemini/
```

### Claude
```bash
npm install -g @anthropic-ai/claude-code
claude login
# Credentials stored in ~/.claude/
```

Once authenticated, run `./setup-creds.sh` to verify, then `docker-compose up` will mount those directories into the container automatically.

### Quick login verification

Before running jobs, you can smoke-test the synced auth in the worker container:

```bash
docker-compose exec -T dev-worker-node sh -lc "claude -p 'say ok'"
docker-compose exec -T dev-worker-node sh -lc "codex --version"
docker-compose exec -T dev-worker-node sh -lc "gemini --yolo 'say ok'"
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
