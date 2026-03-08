#!/usr/bin/env bash
# Register a project and submit a job from local project files.
# Date edited: 2026-02-25 (project target_branch is now source of truth for submitted jobs)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="${ROOT_DIR}/projects/dummy-repo"
PROJECT_ID_OVERRIDE=""
API_URL_OVERRIDE=""
API_KEY_OVERRIDE=""
PRD_FILE=""
PRD_CONTENT=""
TARGET_BRANCH=""
SKIP_REGISTER="0"

usage() {
  cat <<'USAGE'
Usage:
  scripts/register_and_submit_job.sh [options]

Options:
  --project-dir <path>    Project directory with config.yaml, .env, system.md
  --project-id <id>       Override project_id (default: DEV_WORKER_PROJECT_ID or folder name)
  --api-url <url>         Override API base URL (default: DEV_WORKER_API_URL or http://localhost:5100)
  --api-key <key>         Override API key (default: DEV_WORKER_API_KEY or API_KEY from root .env)
  --prd-file <path>       PRD content file for job payload
  --prd-content <text>    PRD content inline (used when --prd-file is not set)
  --target-branch <name>  Override project target_branch at registration time
  --skip-register         Skip POST /api/v1/projects and only submit job
  -h, --help              Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)   PROJECT_DIR="$2"; shift 2 ;;
    --project-id)    PROJECT_ID_OVERRIDE="$2"; shift 2 ;;
    --api-url)       API_URL_OVERRIDE="$2"; shift 2 ;;
    --api-key)       API_KEY_OVERRIDE="$2"; shift 2 ;;
    --prd-file)      PRD_FILE="$2"; shift 2 ;;
    --prd-content)   PRD_CONTENT="$2"; shift 2 ;;
    --target-branch) TARGET_BRANCH="$2"; shift 2 ;;
    --skip-register) SKIP_REGISTER="1"; shift ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

if [[ -n "$PRD_FILE" && ! -f "$PRD_FILE" ]]; then
  echo "PRD file not found: $PRD_FILE" >&2
  exit 1
fi

if [[ -z "$PRD_FILE" && -z "$PRD_CONTENT" ]]; then
  PRD_CONTENT="Smoke test: create a no-op change and report validation steps."
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cd "$ROOT_DIR"
poetry run python - "$ROOT_DIR" "$PROJECT_DIR" "$PROJECT_ID_OVERRIDE" "$API_URL_OVERRIDE" "$API_KEY_OVERRIDE" "$PRD_FILE" "$PRD_CONTENT" "$TARGET_BRANCH" "$TMP_DIR" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

root_dir = Path(sys.argv[1])
project_dir = Path(sys.argv[2])
project_id_override = sys.argv[3].strip()
api_url_override = sys.argv[4].strip()
api_key_override = sys.argv[5].strip()
prd_file = sys.argv[6].strip()
prd_content_inline = sys.argv[7]
target_branch_override = sys.argv[8].strip()
tmp_dir = Path(sys.argv[9])

config_path = project_dir / "config.yaml"
project_env_path = project_dir / ".env"
system_path = project_dir / "system.md"
root_env_path = root_dir / ".env"

if not config_path.is_file():
    raise SystemExit(f"Missing config.yaml: {config_path}")

config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
if not isinstance(config_data, dict):
    raise SystemExit(f"Invalid YAML object in {config_path}")

project_env = dotenv_values(project_env_path) if project_env_path.is_file() else {}
root_env = dotenv_values(root_env_path) if root_env_path.is_file() else {}

project_id = project_id_override or str(project_env.get("DEV_WORKER_PROJECT_ID") or project_dir.name)
api_url = api_url_override or str(project_env.get("DEV_WORKER_API_URL") or root_env.get("DEV_WORKER_API_URL") or "http://localhost:5100")
api_key = api_key_override or str(project_env.get("DEV_WORKER_API_KEY") or root_env.get("DEV_WORKER_API_KEY") or root_env.get("API_KEY") or "")

if not api_key:
    raise SystemExit("Missing API key. Set DEV_WORKER_API_KEY in project .env, API_KEY in root .env, or pass --api-key.")

repository_url = config_data.get("repository_url")
if not isinstance(repository_url, str) or not repository_url.strip():
    raise SystemExit(f"Missing repository_url in {config_path}")

cli_client = str(config_data.get("cli_client") or "codex")
if cli_client not in {"codex", "gemini", "claude"}:
    raise SystemExit(f"Invalid cli_client '{cli_client}' in {config_path}")

target_branch_raw = target_branch_override or config_data.get("target_branch")
if not isinstance(target_branch_raw, str) or not target_branch_raw.strip():
    raise SystemExit(
        f"Missing target_branch in {config_path}. "
        "Set target_branch in config.yaml or pass --target-branch."
    )
target_branch = target_branch_raw.strip()

system_instructions = ""
if system_path.is_file():
    system_instructions = system_path.read_text(encoding="utf-8").strip()

excluded_env_keys = {"DEV_WORKER_API_URL", "DEV_WORKER_API_KEY", "DEV_WORKER_PROJECT_ID"}
env_vars = {}
for key, value in project_env.items():
    if key in excluded_env_keys:
        continue
    if value is None:
        continue
    env_vars[key] = str(value)

register_payload: dict[str, Any] = {
    "project_id": project_id,
    "repository_url": repository_url.strip(),
    "target_branch": target_branch,
    "cli_client": cli_client,
    "cli_model": config_data.get("cli_model"),
    "cli_effort": config_data.get("cli_effort"),
    "cli_flags": config_data.get("cli_flags"),
    "system_instructions": system_instructions if system_instructions else None,
    "env_vars": env_vars,
}
if config_data.get("pr_reviewer_email") is not None:
    register_payload["pr_reviewer_email"] = config_data.get("pr_reviewer_email")

if prd_file:
    prd_content = Path(prd_file).read_text(encoding="utf-8").strip()
else:
    prd_content = prd_content_inline.strip()
if not prd_content:
    raise SystemExit("PRD content is empty. Provide --prd-file or --prd-content.")

job_payload = {
    "project_id": project_id,
    "prd_content": prd_content,
}

(tmp_dir / "register_payload.json").write_text(json.dumps(register_payload, ensure_ascii=True), encoding="utf-8")
(tmp_dir / "job_payload.json").write_text(json.dumps(job_payload, ensure_ascii=True), encoding="utf-8")

meta = {
    "api_url": api_url.rstrip("/"),
    "api_key": api_key,
    "project_id": project_id,
    "project_dir": str(project_dir),
    "env_var_count": len(env_vars),
}
(tmp_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=True), encoding="utf-8")
PY

META_JSON="$TMP_DIR/meta.json"
API_URL="$(poetry run python -c 'import json,sys;print(json.load(open(sys.argv[1]))["api_url"])' "$META_JSON")"
API_KEY="$(poetry run python -c 'import json,sys;print(json.load(open(sys.argv[1]))["api_key"])' "$META_JSON")"
PROJECT_ID="$(poetry run python -c 'import json,sys;print(json.load(open(sys.argv[1]))["project_id"])' "$META_JSON")"
ENV_VAR_COUNT="$(poetry run python -c 'import json,sys;print(json.load(open(sys.argv[1]))["env_var_count"])' "$META_JSON")"

echo "Project directory: $PROJECT_DIR"
echo "Project id: ${PROJECT_ID}"
echo "API URL: ${API_URL}"
echo "Project env vars in payload: ${ENV_VAR_COUNT}"

if [[ "$SKIP_REGISTER" != "1" ]]; then
  REGISTER_STATUS="$(curl -sS -o "$TMP_DIR/register_response.json" -w "%{http_code}" \
    -X POST "${API_URL}/api/v1/projects" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    --data @"$TMP_DIR/register_payload.json")"

  if [[ "$REGISTER_STATUS" == "201" ]]; then
    echo "Project registration: created (201)"
  elif [[ "$REGISTER_STATUS" == "409" ]]; then
    echo "Project registration: already exists (409), continuing"
  else
    echo "Project registration failed: HTTP ${REGISTER_STATUS}" >&2
    cat "$TMP_DIR/register_response.json" >&2 || true
    exit 1
  fi
fi

SUBMIT_STATUS="$(curl -sS -o "$TMP_DIR/job_response.json" -w "%{http_code}" \
  -X POST "${API_URL}/api/v1/jobs" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  --data @"$TMP_DIR/job_payload.json")"

if [[ "$SUBMIT_STATUS" != "200" ]]; then
  echo "Job submission failed: HTTP ${SUBMIT_STATUS}" >&2
  cat "$TMP_DIR/job_response.json" >&2 || true
  exit 1
fi

JOB_ID="$(poetry run python -c 'import json,sys;print((json.load(open(sys.argv[1]))).get("id",""))' "$TMP_DIR/job_response.json")"
JOB_STATUS="$(poetry run python -c 'import json,sys;print((json.load(open(sys.argv[1]))).get("status",""))' "$TMP_DIR/job_response.json")"

echo "Job submitted: id=${JOB_ID} status=${JOB_STATUS}"
echo "Inspect: curl -H \"X-API-Key: <key>\" ${API_URL}/api/v1/jobs/${JOB_ID}"
