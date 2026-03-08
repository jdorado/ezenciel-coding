"""Worker engine: claims queued jobs, runs CLI coding agents, commits and opens PRs.
Last edited: 2026-02-27 (auto-run repo standard pre-setup script when present)
"""
from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from src.config import _resolve_dir, load_project_configs, logger, settings
from src.database.repository import JobRepository, get_repository

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mABCDEFGHJKSTfnsulh]")

WORKER_NAME = f"ezenciel-worker:{socket.gethostname()}"
_WORKER_RESULT_FILE = ".worker_result.json"
_DEVJOB_TRACKER_FILE = ".devjob_tracker.md"
_SELF_JOB_SUBMISSION_ENV_KEYS = (
    "DEV_WORKER_API_URL",
    "DEV_WORKER_API_KEY",
    "DEV_WORKER_PROJECT_ID",
    "WORKER_API_URL",
    "WORKER_API_KEY",
    "WORKER_PROJECT_ID",
)
_PRE_JOB_SETUP_TIMEOUT_SECONDS_DEFAULT = 1800
_DEFAULT_PRE_JOB_SETUP_SCRIPT_CANDIDATES = (
    "deploy/scripts/worker_pre_setup.sh",
    "scripts/worker_pre_setup.sh",
)

# Built-in non-interactive / full-permission flags per CLI client.
_CLIENT_AUTO_FLAGS: dict[str, list[str]] = {
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "claude": ["-p", "--dangerously-skip-permissions"],
    "gemini": ["--yolo"],
}

# Default model per CLI client. Used when project config omits cli_model.
# Empty string means no --model flag — the CLI uses its own built-in default.
_CLIENT_DEFAULT_MODELS: dict[str, str] = {
    "codex": "",
    "claude": "claude-sonnet-4-6",
    "gemini": "",
}

_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "resource_exhausted",
    "capacity exhausted",
    "rateLimitExceeded",
    "MODEL_CAPACITY_EXHAUSTED",
)

_IMPLEMENT_PROMPT = "Please implement the requirements in PRD.md. Make sure to test your code."
_GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def _is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker.lower() in text for marker in _RATE_LIMIT_MARKERS)


class _BlockedError(Exception):
    """Agent signaled a blocker or produced no changes — needs human attention."""


def _strip_self_job_submission_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Remove env keys that would let worker jobs enqueue new worker jobs."""
    sanitized = dict(env)
    removed: list[str] = []
    for key in _SELF_JOB_SUBMISSION_ENV_KEYS:
        if key in sanitized:
            removed.append(key)
            sanitized.pop(key, None)
    return sanitized, removed


def _find_default_pre_job_setup_command(workspace_dir: str) -> Optional[str]:
    for relative_path in _DEFAULT_PRE_JOB_SETUP_SCRIPT_CANDIDATES:
        candidate = os.path.join(workspace_dir, relative_path)
        if os.path.isfile(candidate):
            return f"bash {shlex.quote(relative_path)}"
    return None


def _resolve_pre_job_setup_commands(project: dict[str, Any], *, workspace_dir: Optional[str] = None) -> list[str]:
    """Resolve deterministic setup commands.

    Precedence:
    1) project config overrides (`pre_job_setup_command(s)`)
    2) default repo script convention if present:
       - deploy/scripts/worker_pre_setup.sh
       - scripts/worker_pre_setup.sh
    """
    commands: list[str] = []

    single = project.get("pre_job_setup_command")
    if isinstance(single, str):
        normalized = single.strip()
        if normalized:
            commands.append(normalized)

    multi = project.get("pre_job_setup_commands")
    if isinstance(multi, list):
        for entry in multi:
            if isinstance(entry, str):
                normalized = entry.strip()
                if normalized:
                    commands.append(normalized)

    if commands:
        return commands

    if workspace_dir:
        detected = _find_default_pre_job_setup_command(workspace_dir)
        if detected:
            return [detected]

    return []


def _resolve_pre_job_setup_timeout_seconds(project: dict[str, Any]) -> int:
    raw = project.get("pre_job_setup_timeout_seconds")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _PRE_JOB_SETUP_TIMEOUT_SECONDS_DEFAULT


def _build_pre_job_setup_instruction(commands: list[str]) -> str:
    lines: list[str] = [
        "## Pre-Setup Contract",
        "The worker already executed deterministic repository setup before your run.",
        "Do not re-install dependencies or re-bootstrap runtime unless strictly required by a failing test tied to this PRD.",
        "If setup appears broken, write `.worker_result.json` with `type=blocked` describing setup failure and stop.",
        "Executed setup commands:",
    ]
    lines.extend(f"- `{command}`" for command in commands)
    return "\n".join(lines)


def _read_worker_result(path: str) -> Optional[dict]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("Failed to read worker result file {}: {}", path, exc)
        return None


def _extract_pr_url(output: str) -> Optional[str]:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("https://github.com/") and "/pull/" in line:
            return line
    return None


def _normalize_pr_reviewer_email(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _extract_github_login(value: str) -> Optional[str]:
    text = value.strip()
    if not text:
        return None
    candidate = text.splitlines()[-1].strip()
    if not candidate or candidate.lower() == "null":
        return None
    if not _GITHUB_LOGIN_RE.fullmatch(candidate):
        return None
    return candidate


def _resolve_github_reviewer_login_by_email(
    reviewer_email: str,
    *,
    run_cmd: Callable[..., str],
    workspace_dir: str,
    job_id: str,
    env: dict[str, str],
    commands_ran: list[str],
) -> Optional[str]:
    lookup_output = run_cmd(
        [
            "gh",
            "api",
            "search/users",
            "-f",
            f"q={reviewer_email} in:email",
            "--jq",
            '.items[0].login // ""',
        ],
        cwd=workspace_dir,
        job_id=job_id,
        env=env,
        commands_ran=commands_ran,
    )
    return _extract_github_login(lookup_output)


def _normalize_markdown_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _extract_markdown_section(markdown_text: str, header: str) -> Optional[str]:
    target = _normalize_markdown_header(header)
    lines = markdown_text.splitlines()
    collecting = False
    collected: list[str] = []

    for line in lines:
        header_match = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if header_match:
            current_header = _normalize_markdown_header(header_match.group(1))
            if collecting:
                break
            if current_header == target:
                collecting = True
                continue
        if collecting:
            collected.append(line)

    if not collecting:
        return None

    section = "\n".join(collected).strip()
    return section or None


def _load_qa_evidence_from_tracker(workspace_dir: str) -> Optional[str]:
    tracker_path = os.path.join(workspace_dir, _DEVJOB_TRACKER_FILE)
    if not os.path.exists(tracker_path):
        return None

    try:
        with open(tracker_path, encoding="utf-8") as handle:
            tracker_text = handle.read()
    except Exception as exc:
        logger.warning("Failed to read {}: {}", _DEVJOB_TRACKER_FILE, exc)
        return None

    for header in ("QA Evidence", "Verification Run"):
        section = _extract_markdown_section(tracker_text, header)
        if section:
            return section

    return None


def _build_pr_body(*, prd_content: str, job_id: str, project_id: str, job_branch: str, diffstat: str, qa_evidence: str) -> str:
    return (
        f"{prd_content.strip()}\n\n"
        f"## QA Evidence\n"
        f"Source: `{_DEVJOB_TRACKER_FILE}`\n\n"
        f"{qa_evidence.strip()}\n\n"
        f"---\n"
        f"**Job:** `{job_id}` | **Project:** `{project_id}` | **Branch:** `{job_branch}`\n\n"
        f"<details><summary>Diff stat</summary>\n\n```\n{diffstat.strip()}\n```\n</details>"
    )


def _default_agent_instructions() -> str:
    return """

---
## Worker Instructions

After completing your implementation:

1. Keep `.devjob_tracker.md` updated and include a `## QA Evidence` section before finishing.
   - Required format: command + explicit PASS/FAIL result per line.
   - If `## QA Evidence` is not available, at minimum include this in `## Verification Run`.

2. If you encounter a blocker (missing API key, external dependency, decision needed from owner):
   Write `.worker_result.json` in the repo root:
   ```json
   {"type": "blocked", "blockers": ["description of what is needed"], "summary": "brief explanation"}
   ```
   Then stop — do NOT make code changes when blocked.

3. If implementation completes successfully, no action needed.
   The worker will detect your committed/uncommitted changes automatically.

Do NOT write `.worker_result.json` on success.
"""


def _build_agent_instructions(project: dict, *, pre_setup_commands: Optional[list[str]] = None) -> str:
    sections: list[str] = []
    configured_prompt = project.get("system_instructions")
    if isinstance(configured_prompt, str) and configured_prompt.strip():
        sections.append(configured_prompt.strip())
    resolved_pre_setup_commands = pre_setup_commands
    if resolved_pre_setup_commands is None:
        resolved_pre_setup_commands = _resolve_pre_job_setup_commands(project)
    if resolved_pre_setup_commands:
        sections.append(_build_pre_job_setup_instruction(resolved_pre_setup_commands))
    sections.append(_default_agent_instructions().strip())
    return "\n\n".join(sections).strip() + "\n"


def _build_agent_command(
    *,
    cli_client: str,
    cli_model: str,
    cli_effort: str,
    cli_flags: str,
    on_warning: Optional[Callable[[str], None]] = None,
) -> list[str]:
    cmd_parts = [cli_client]
    parsed_cli_flags = shlex.split(cli_flags) if cli_flags else []
    if cli_client == "codex":
        # `codex` top-level launches TUI and requires a terminal.
        # `codex exec` is the non-interactive mode for worker containers.
        cmd_parts.append("exec")
        if "--json" not in parsed_cli_flags:
            # JSON events allow us to keep only codex reasoning lines in worker logs.
            cmd_parts.append("--json")

    cmd_parts.extend(_CLIENT_AUTO_FLAGS.get(cli_client, []))
    if cli_model:
        cmd_parts.extend(["--model", cli_model])
    if cli_client == "codex" and cli_effort and on_warning is not None:
        on_warning("cli_effort is ignored for codex exec mode; set raw codex overrides in cli_flags if needed.")
    cmd_parts.extend(parsed_cli_flags)
    cmd_parts.append(_IMPLEMENT_PROMPT)
    return cmd_parts


def _is_codex_json_command(cmd: list[str]) -> bool:
    return len(cmd) >= 2 and cmd[0] == "codex" and cmd[1] == "exec" and "--json" in cmd


def _extract_codex_json_payload(raw_line: str) -> Optional[dict]:
    stripped = raw_line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _extract_codex_item_text(item: dict) -> Optional[str]:
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    if isinstance(text, list):
        parts: list[str] = []
        for chunk in text:
            if isinstance(chunk, str) and chunk.strip():
                parts.append(chunk.strip())
            elif isinstance(chunk, dict):
                chunk_text = chunk.get("text")
                if isinstance(chunk_text, str) and chunk_text.strip():
                    parts.append(chunk_text.strip())
        if parts:
            return "\n".join(parts)
    return None


def _extract_codex_reasoning_line(raw_line: str) -> Optional[str]:
    payload = _extract_codex_json_payload(raw_line)
    if payload is None:
        return None
    if payload.get("type") != "item.completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return None
    return _extract_codex_item_text(item)


def _extract_codex_agent_message_line(raw_line: str) -> Optional[str]:
    payload = _extract_codex_json_payload(raw_line)
    if payload is None:
        return None
    if payload.get("type") != "item.completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    return _extract_codex_item_text(item)


def _extract_codex_error_line(raw_line: str) -> Optional[str]:
    payload = _extract_codex_json_payload(raw_line)
    if payload is None:
        return None
    if payload.get("type") == "error":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None
    if payload.get("type") != "item.completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "error":
        return None
    message = item.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


class WorkerEngine:
    def __init__(self, repository: Optional[JobRepository] = None):
        self.repo = repository or get_repository()
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Worker engine started worker_name={}", WORKER_NAME)

    def stop(self) -> None:
        self.is_running = False
        if self.thread:
            self.thread.join()
        logger.info("Worker engine stopped")

    def _run_loop(self) -> None:
        while self.is_running:
            self._recover_stale_jobs()
            self._print_heartbeat()
            self._process_next_job()
            time.sleep(settings.poll_interval_seconds)

    def _recover_stale_jobs(self) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=settings.job_timeout_minutes)
        stale_jobs = self.repo.list_stale(cutoff)

        for job in stale_jobs:
            logger.warning(
                "[job:{}] stale — no heartbeat for {}m, marking failed",
                job["id"],
                settings.job_timeout_minutes,
            )
            self.repo.update(
                job["id"],
                {
                    "status": "failed",
                    "phase": "done",
                    "result": {
                        "type": "failed",
                        "error": f"Job timed out after {settings.job_timeout_minutes} minutes (worker likely crashed)",
                        "error_type": "Timeout",
                    },
                    "completed_at": datetime.utcnow(),
                },
            )

    def _print_heartbeat(self) -> None:
        queued_present = bool(self.repo.list(status="queued", limit=1))
        running_present = bool(self.repo.list(status="in_progress", limit=1))
        logger.info("[heartbeat] queued_present={} running_present={}", queued_present, running_present)

    def _process_next_job(self) -> None:
        job = self.repo.claim_next(worker_id=WORKER_NAME)
        if not job:
            return

        job_id = job["id"]
        logger.info(
            "[job:{}] claimed project={} target_branch={} attempt={}",
            job_id,
            job["project_id"],
            job.get("target_branch"),
            int(job.get("retry_count", 0)) + 1,
        )

        try:
            self._execute_job(job_id)
            latest = self.repo.get(job_id)
            if latest and latest.get("status") != "cancelled":
                self.repo.update(job_id, {"status": "success", "phase": "done"})
                self._append_log(job_id, "\nJob completed successfully.")

        except _BlockedError as exc:
            latest = self.repo.get(job_id)
            if latest and latest.get("status") == "cancelled":
                logger.info("[job:{}] was cancelled mid-run, leaving as cancelled", job_id)
                return
            self.repo.update(job_id, {"status": "blocked", "phase": "done"})
            self._append_log(job_id, f"\nJob blocked: {str(exc)}")

        except Exception as exc:
            latest = self.repo.get(job_id)
            if latest and latest.get("status") == "cancelled":
                logger.info("[job:{}] was cancelled mid-run, leaving as cancelled", job_id)
                return

            retry_count = int((latest or job).get("retry_count", 0))
            if _is_rate_limit_error(exc) and retry_count < settings.job_max_retries:
                retry_at = datetime.utcnow() + timedelta(minutes=settings.job_retry_delay_minutes)
                self.repo.update(
                    job_id,
                    {
                        "retry_count": retry_count + 1,
                        "status": "queued",
                        "phase": None,
                        "retry_after": retry_at,
                    },
                )
                logger.info(
                    "[job:{}] rate-limit hit — retry {}/{} scheduled at {}",
                    job_id,
                    retry_count + 1,
                    settings.job_max_retries,
                    retry_at.strftime("%H:%M:%SZ"),
                )
                return

            latest_result = (latest or {}).get("result")
            updates: Dict[str, Any] = {"status": "failed", "phase": "done"}
            if not latest_result:
                updates["result"] = {"type": "failed", "error": str(exc), "error_type": type(exc).__name__}
            self.repo.update(job_id, updates)
            self._append_log(job_id, f"\nJob failed: {str(exc)}")

        finally:
            latest = self.repo.get(job_id)
            if latest and latest.get("status") not in ("queued", "cancelled"):
                latest = self.repo.update(job_id, {"completed_at": datetime.utcnow()}) or latest
                logger.info("[job:{}] done status={}", job_id, latest.get("status"))

    def _set_phase(self, job_id: str, phase: str) -> None:
        self.repo.update(job_id, {"phase": phase})
        logger.info("[job:{}] phase={}", job_id, phase)

    def _append_job_logs(self, job_id: str, text: str) -> None:
        if not text:
            return
        job = self.repo.get(job_id)
        if not job:
            return
        current = job.get("logs") or ""
        self.repo.update(job_id, {"logs": current + text})

    def _append_log(self, job_id: str, message: str) -> None:
        logger.info(message)
        self._append_job_logs(job_id, message + "\n")

    def _sync_workspace(
        self,
        *,
        job_id: str,
        workspace_dir: str,
        target_branch: str,
        authed_url: str,
        secrets: list[str],
        commands_ran: list[str],
    ) -> None:
        git_dir = os.path.join(workspace_dir, ".git")
        if os.path.exists(git_dir):
            self._append_log(job_id, f"Fetching latest from {target_branch}...")
            self._run_cmd(["git", "remote", "set-url", "origin", authed_url], cwd=workspace_dir, job_id=job_id, secrets=secrets, commands_ran=commands_ran)
            self._run_cmd(["git", "fetch", "origin"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
            self._append_log(job_id, "Cleaning workspace from previous job artifacts...")
            self._run_cmd(["git", "reset", "--hard", "HEAD"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
            self._run_cmd(["git", "clean", "-fd"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
            self._run_cmd(
                ["git", "checkout", "-B", target_branch, f"origin/{target_branch}"],
                cwd=workspace_dir,
                job_id=job_id,
                commands_ran=commands_ran,
            )
            return

        self._append_log(job_id, f"Cloning repository into {workspace_dir}...")
        self._run_cmd(["git", "clone", "--branch", target_branch, authed_url, workspace_dir], cwd=".", job_id=job_id, secrets=secrets, commands_ran=commands_ran)

    def _execute_job(self, job_id: str) -> None:
        job = self.repo.get(job_id)
        if not job:
            raise RuntimeError(f"Job {job_id} not found")

        projects = load_project_configs()
        project = projects.get(job["project_id"])
        if not project:
            raise ValueError(f"Project configuration for '{job['project_id']}' not found.")

        repo_url = project.get("repository_url")
        if not repo_url:
            raise ValueError("repository_url not defined in project config")

        # Build environment
        env = os.environ.copy()
        if "env_vars" in project:
            env.update(project["env_vars"])
        if job.get("env_vars_override"):
            env.update(job["env_vars_override"])
        env, removed_self_job_keys = _strip_self_job_submission_env(env)
        if removed_self_job_keys:
            self._append_log(
                job_id,
                "Removed worker job-submission env keys for this run: "
                + ", ".join(sorted(removed_self_job_keys)),
            )

        github_token = env.get("GITHUB_TOKEN")
        secrets = [secret for secret in [github_token] if secret]

        # Inject token into URL for git operations (never logged — redacted via secrets)
        if github_token and repo_url.startswith("https://github.com/"):
            authed_url = repo_url.replace("https://github.com/", f"https://oauth2:{github_token}@github.com/")
        else:
            authed_url = repo_url

        target_branch = job.get("target_branch") or "main"
        job_branch = f"worker/{job_id[:8]}"
        reviewer_email = _normalize_pr_reviewer_email(project.get("pr_reviewer_email"))

        workspaces_dir = _resolve_dir(settings.workspaces_dir)
        workspace_dir = os.path.join(workspaces_dir, job["project_id"])
        os.makedirs(workspaces_dir, exist_ok=True)

        # Resume checkpoint: if PR already submitted, skip straight to success
        if isinstance(job.get("result"), dict) and job["result"].get("pr_url"):
            pr_url = job["result"]["pr_url"]
            self._append_log(job_id, f"Resuming: PR already exists at {pr_url} — marking success.")
            return

        commands_ran: list[str] = []

        # 1. Sync workspace
        self._set_phase(job_id, "syncing")
        self._sync_workspace(
            job_id=job_id,
            workspace_dir=workspace_dir,
            target_branch=target_branch,
            authed_url=authed_url,
            secrets=secrets,
            commands_ran=commands_ran,
        )

        self._run_cmd(["git", "config", "user.name", settings.git_user_name], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
        self._run_cmd(["git", "config", "user.email", settings.git_user_email], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
        self._run_cmd(["git", "checkout", "-B", job_branch], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
        self.repo.update(job_id, {"branch_name": job_branch})

        pre_setup_commands = _resolve_pre_job_setup_commands(project, workspace_dir=workspace_dir)
        pre_setup_timeout_seconds = _resolve_pre_job_setup_timeout_seconds(project)
        self._run_pre_job_setup(
            job_id=job_id,
            pre_setup_commands=pre_setup_commands,
            pre_setup_timeout_seconds=pre_setup_timeout_seconds,
            workspace_dir=workspace_dir,
            env=env,
            commands_ran=commands_ran,
        )

        # Clean any stale worker result file from previous run
        result_file_path = os.path.join(workspace_dir, _WORKER_RESULT_FILE)
        if os.path.exists(result_file_path):
            os.remove(result_file_path)

        pre_head = self._run_cmd(["git", "rev-parse", "HEAD"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran).strip()

        # 2. Execute CLI coding agent
        self._set_phase(job_id, "executing")
        cli_client = project.get("cli_client", "codex")
        cli_model = project.get("cli_model") or _CLIENT_DEFAULT_MODELS.get(cli_client, "")
        cli_effort = project.get("cli_effort", "")
        cli_flags = project.get("cli_flags", "")

        prd_file_path = os.path.join(workspace_dir, "PRD.md")
        with open(prd_file_path, "w", encoding="utf-8") as handle:
            handle.write(job["prd_content"])
            handle.write("\n")
            handle.write(_build_agent_instructions(project, pre_setup_commands=pre_setup_commands))

        cmd_parts = _build_agent_command(
            cli_client=cli_client,
            cli_model=cli_model,
            cli_effort=cli_effort,
            cli_flags=cli_flags,
            on_warning=lambda message: self._append_log(job_id, message),
        )

        self._append_log(job_id, f"Running: {' '.join(cmd_parts)}")
        try:
            self._run_cmd(cmd_parts, cwd=workspace_dir, job_id=job_id, env=env, commands_ran=commands_ran)
        except Exception as exc:
            self._append_log(job_id, f"Agent execution failed: {str(exc)}")
            raise

        # 3. Read agent result file — agent writes this on blocker
        worker_result = _read_worker_result(result_file_path)
        if worker_result:
            result_type = worker_result.get("type", "")
            if result_type == "blocked":
                blockers = worker_result.get("blockers", [])
                summary = worker_result.get("summary", "Agent signaled blocker.")
                self.repo.update(
                    job_id,
                    {
                        "result": {
                            "type": "blocked",
                            "blockers": blockers,
                            "summary": summary,
                            "commands_ran": commands_ran,
                        }
                    },
                )
                joined = "; ".join(blockers) if blockers else summary
                raise _BlockedError(f"Agent signaled blocker: {joined}")

        # 4. No-changes detection
        qa_evidence = _load_qa_evidence_from_tracker(workspace_dir)
        if not qa_evidence:
            missing_qa_message = (
                f"Missing QA evidence in `{_DEVJOB_TRACKER_FILE}`. "
                "Add `## QA Evidence` or `## Verification Run` with exact commands and pass/fail outcomes."
            )
            self.repo.update(
                job_id,
                {
                    "result": {
                        "type": "blocked",
                        "blockers": [missing_qa_message],
                        "summary": missing_qa_message,
                        "commands_ran": commands_ran,
                    }
                },
            )
            raise _BlockedError(missing_qa_message)

        post_head = self._run_cmd(["git", "rev-parse", "HEAD"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran).strip()
        status_output = self._run_cmd(["git", "status", "--porcelain"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
        has_uncommitted = bool(status_output.strip())
        has_committed = pre_head != post_head

        if not has_uncommitted and not has_committed:
            self.repo.update(
                job_id,
                {
                    "result": {
                        "type": "blocked",
                        "blockers": ["Agent produced no file changes"],
                        "summary": "No changes were made to the repository.",
                        "commands_ran": commands_ran,
                    }
                },
            )
            raise _BlockedError("Agent produced no file changes")

        # 5. Commit
        self._set_phase(job_id, "committing")
        if has_uncommitted:
            self._run_cmd(["git", "add", "."], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
            # Unstage worker-owned files that must never be committed to any target repo.
            # Use subprocess directly (check=False) so missing files don't raise.
            for worker_file in ("PRD.md", ".devjob_tracker.md"):
                subprocess.run(["git", "restore", "--staged", worker_file], cwd=workspace_dir, capture_output=True)
            diffstat = self._run_cmd(["git", "diff", "--cached", "--stat"], cwd=workspace_dir, job_id=job_id, commands_ran=commands_ran)
            self._run_cmd(
                ["git", "commit", "-m", f"devworker job {job_id[:8]}: implement PRD"],
                cwd=workspace_dir,
                job_id=job_id,
                commands_ran=commands_ran,
            )
        else:
            diffstat = self._run_cmd(
                ["git", "show", "--stat", "--pretty=format:", "HEAD"],
                cwd=workspace_dir,
                job_id=job_id,
                commands_ran=commands_ran,
            )

        # 6. Push
        self._set_phase(job_id, "pushing")
        push_env = {**env}
        if github_token:
            push_env["GITHUB_TOKEN"] = github_token
        self._run_cmd(
            ["git", "push", "-u", "origin", job_branch],
            cwd=workspace_dir,
            job_id=job_id,
            env=push_env,
            secrets=secrets,
            commands_ran=commands_ran,
        )

        # 7. PR creation (best-effort)
        self._set_phase(job_id, "creating_pr")
        pr_url = None
        if shutil.which("gh"):
            try:
                latest_job = self.repo.get(job_id) or job
                prd_content = latest_job.get("prd_content", "")
                pr_body = _build_pr_body(
                    prd_content=prd_content,
                    job_id=job_id,
                    project_id=job["project_id"],
                    job_branch=job_branch,
                    diffstat=diffstat,
                    qa_evidence=qa_evidence,
                )
                pr_output = self._run_cmd(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--base",
                        target_branch,
                        "--head",
                        job_branch,
                        "--title",
                        f"[Worker] {prd_content.splitlines()[0].lstrip('# ').strip()[:72] if prd_content.splitlines() else 'Worker update'}",
                        "--body",
                        pr_body,
                    ],
                    cwd=workspace_dir,
                    job_id=job_id,
                    env=push_env,
                    commands_ran=commands_ran,
                )
                pr_url = _extract_pr_url(pr_output)
                if pr_url and reviewer_email:
                    reviewer_login: Optional[str] = None
                    try:
                        reviewer_login = _resolve_github_reviewer_login_by_email(
                            reviewer_email,
                            run_cmd=self._run_cmd,
                            workspace_dir=workspace_dir,
                            job_id=job_id,
                            env=push_env,
                            commands_ran=commands_ran,
                        )
                    except Exception as exc:
                        self._append_log(job_id, f"Reviewer lookup failed (non-fatal): {exc}")

                    if reviewer_login:
                        try:
                            self._run_cmd(
                                ["gh", "pr", "edit", pr_url, "--add-reviewer", reviewer_login],
                                cwd=workspace_dir,
                                job_id=job_id,
                                env=push_env,
                                commands_ran=commands_ran,
                            )
                        except Exception as exc:
                            self._append_log(job_id, f"PR reviewer request failed (non-fatal): {exc}")
                    else:
                        self._append_log(
                            job_id,
                            f"No GitHub user found for reviewer email '{reviewer_email}' — skipping reviewer request.",
                        )
            except Exception as exc:
                self._append_log(job_id, f"PR creation failed (non-fatal): {exc}")
        else:
            self._append_log(job_id, "gh CLI not found — skipping PR creation.")

        # 8. Persist structured result
        self.repo.update(
            job_id,
            {
                "result": {
                    "type": "success",
                    "pr_url": pr_url,
                    "branch": job_branch,
                    "diffstat": diffstat.strip() if diffstat else "",
                    "commands_ran": commands_ran,
                    "summary": f"Job {job_id[:8]} completed — branch {job_branch}" + (f", PR: {pr_url}" if pr_url else ""),
                }
            },
        )

    def _run_cmd(
        self,
        cmd: list[str],
        cwd: str,
        job_id: str,
        env: Optional[Dict[str, str]] = None,
        secrets: Optional[list[str]] = None,
        commands_ran: Optional[list[str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> str:
        protected = [secret for secret in (secrets or []) if secret]
        codex_json_mode = _is_codex_json_command(cmd)

        def _redact(text: str) -> str:
            redacted = _ANSI_RE.sub("", text)
            for secret in protected:
                redacted = redacted.replace(secret, "***")
            return redacted

        safe_preview = _redact(" ".join(str(part) for part in cmd))
        logger.info("$ {}", safe_preview)
        if commands_ran is not None:
            commands_ran.append(safe_preview)

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        line_q: queue.Queue[Optional[str]] = queue.Queue()

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                line_q.put(line)
            line_q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        output_parts: list[str] = []
        codex_diagnostics: list[str] = []
        codex_assistant_fallback: list[str] = []
        saw_codex_thinking = False
        last_cancel_check = time.time()
        started_at = time.time()

        while True:
            try:
                line = line_q.get(timeout=0.2)
            except queue.Empty:
                if timeout_seconds is not None and (time.time() - started_at) > timeout_seconds:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    raise RuntimeError(f"Command timed out after {timeout_seconds}s: {safe_preview}")
                # Check for cancellation every 5 seconds.
                if time.time() - last_cancel_check >= 5:
                    last_cancel_check = time.time()
                    latest = self.repo.get(job_id)
                    if latest and latest.get("status") == "cancelled":
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        proc.wait()
                        raise RuntimeError("Job cancelled")
                continue

            if line is None:
                break

            safe_line = _redact(line.rstrip())
            if codex_json_mode:
                thinking = _extract_codex_reasoning_line(line.rstrip())
                if thinking is not None:
                    redacted_thinking = _redact(thinking)
                    output_parts.append(f"[thinking] {redacted_thinking}\n")
                    logger.info("[thinking] {}", redacted_thinking)
                    saw_codex_thinking = True
                    continue

                codex_error = _extract_codex_error_line(line.rstrip())
                if codex_error is not None:
                    redacted_error = _redact(codex_error)
                    output_parts.append(f"[codex-error] {redacted_error}\n")
                    logger.info("[codex-error] {}", redacted_error)
                    continue

                agent_message = _extract_codex_agent_message_line(line.rstrip())
                if agent_message is not None:
                    codex_assistant_fallback.append(_redact(agent_message))
                elif safe_line:
                    # Keep non-reasoning lines only for failure diagnostics.
                    codex_diagnostics.append(safe_line)
                    if len(codex_diagnostics) > 50:
                        codex_diagnostics.pop(0)
                continue

            output_parts.append(safe_line + "\n")
            logger.info(safe_line)

        return_code = proc.wait()
        if codex_json_mode and not saw_codex_thinking and codex_assistant_fallback:
            for fallback in codex_assistant_fallback:
                output_parts.append(f"[assistant] {fallback}\n")
                logger.info("[assistant] {}", fallback)
        output = "".join(output_parts)

        if output.strip():
            self._append_job_logs(job_id, output)

        if return_code != 0:
            if codex_json_mode and codex_diagnostics:
                diagnostic_text = "\n".join(codex_diagnostics) + "\n"
                self._append_job_logs(job_id, diagnostic_text)
            error_msg = f"Command failed (exit {return_code}): {safe_preview}"
            self._append_job_logs(job_id, error_msg + "\n")
            raise RuntimeError(error_msg)

        return output

    def _run_pre_job_setup(
        self,
        *,
        job_id: str,
        pre_setup_commands: list[str],
        pre_setup_timeout_seconds: int,
        workspace_dir: str,
        env: dict[str, str],
        commands_ran: list[str],
    ) -> None:
        if not pre_setup_commands:
            return

        self._set_phase(job_id, "pre_setup")
        self._append_log(
            job_id,
            "Running deterministic pre-job setup "
            f"({len(pre_setup_commands)} command(s), timeout={pre_setup_timeout_seconds}s each).",
        )

        for index, command in enumerate(pre_setup_commands, start=1):
            self._append_log(job_id, f"Pre-setup [{index}/{len(pre_setup_commands)}]: {command}")
            try:
                self._run_cmd(
                    ["bash", "-lc", command],
                    cwd=workspace_dir,
                    job_id=job_id,
                    env=env,
                    commands_ran=commands_ran,
                    timeout_seconds=pre_setup_timeout_seconds,
                )
            except Exception as exc:
                raise _BlockedError(
                    "Pre-job setup failed before coding execution. "
                    f"Command `{command}` error: {exc}"
                ) from exc

# Global instance
worker_engine = WorkerEngine()
