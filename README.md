# ezenciel-coding
<!-- Updated: 2026-02-25 (target_branch is project-level and resolved during job submit) -->

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

## Backend selection (SQLite or optional MongoDB)

By default, the service uses SQLite:

```env
DB_PATH=sqlite:///data/worker.db
```

To use MongoDB as the primary backend instead, set:

```env
MONGODB_URI=mongodb://localhost:27017/ezenciel_coding
```

When `MONGODB_URI` is set:
- Jobs are stored in MongoDB (`jobs` collection)
- Project configs are stored in MongoDB (`projects` collection)
- Disk-based `projects/<id>/config.yaml` is not used

## API endpoint: register project

Use `POST /api/v1/projects` with `X-API-Key`.

```bash
curl -X POST http://localhost:8080/api/v1/projects \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-repo",
    "repository_url": "https://github.com/my-org/my-repo.git",
    "target_branch": "main",
    "cli_client": "codex",
    "cli_model": "gpt-4o",
    "cli_effort": null,
    "cli_flags": null,
    "pr_reviewer_email": "reviewer@example.com",
    "system_instructions": "optional per-project prompt",
    "env_vars": {
      "GITHUB_TOKEN": "..."
    }
  }'
```

Returns `201 Created` on success and `409` if `project_id` already exists.

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
# Credentials natively tied to your local OS Keychain (can't be synced via secure copy)
```

Once authenticated, run `./scripts/sync_creds.sh` to copy `codex` and `gemini` credentials to your VM, then `docker-compose up` will mount those directories into the container.

**Important for Remote VM Deployments (Claude Code only):** 
Because Claude stores authentication tokens securely in the host OS Keychain, copying `~/.claude.json` from a Mac to a Linux VM will **not** transfer the login session. When running `dev-worker-node` on a remote Linux server via Docker, you must initialize a one-time login for Claude interactively from within the running container:

```bash
# SSH into your VM and run this command:
docker exec -it dev-worker-node claude auth login
```
*(Complete the browser login link provided in the terminal to finish setup)*

### Quick login verification

Before running jobs, you can smoke-test the synced auth in the worker container:

```bash
docker exec -it dev-worker-node "claude -p 'say ok'"
docker exec dev-worker-node sh -lc "codex --version"
docker exec dev-worker-node sh -lc "gemini --yolo 'say ok'"
```

## Register a project

SQLite mode (`MONGODB_URI` unset):

Create a directory under `projects/` with a `config.yaml`:

```yaml
# projects/my-repo/config.yaml
repository_url: "https://github.com/my-org/my-repo.git"
target_branch: "main"
cli_client: "codex"       # codex | gemini | claude
cli_model: "gpt-4o"
cli_effort: "high"
cli_flags: "--yes --force"
pr_reviewer_email: "reviewer@example.com"
```

Add a `.env` with credentials the agent needs (e.g. `GITHUB_TOKEN`).

See `projects/dummy-repo/` for a full example.

MongoDB mode (`MONGODB_URI` set):

Register projects with the API:

```bash
curl -X POST http://localhost:8080/api/v1/projects \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-repo",
    "repository_url": "https://github.com/my-org/my-repo.git",
    "target_branch": "main",
    "cli_client": "codex",
    "cli_model": "gpt-4o",
    "pr_reviewer_email": "reviewer@example.com",
    "env_vars": {
      "GITHUB_TOKEN": "..."
    }
  }'
```

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
