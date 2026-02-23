"""Worker engine: claims queued jobs, runs CLI coding agents, commits and opens PRs.
Last edited: 2026-02-23 (timezone-aware datetimes; rich PR body from PRD)
"""
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
from datetime import datetime, timedelta, timezone

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mABCDEFGHJKSTfnsulh]")

import httpx

from src.database.session import SessionLocal
from src.models.job import Job
from src.config import settings, load_project_configs, logger

WORKER_NAME = f"ezenciel-worker:{socket.gethostname()}"
_WORKER_RESULT_FILE = ".worker_result.json"

# Built-in non-interactive / full-permission flags per CLI client.
_CLIENT_AUTO_FLAGS: dict[str, list[str]] = {
    "codex":  ["--dangerously-bypass-approvals-and-sandbox"],
    "claude": ["-p", "--dangerously-skip-permissions"],
    "gemini": ["--yolo"],
}

# Default model per CLI client. Used when project config omits cli_model.
# Empty string means no --model flag — the CLI uses its own built-in default.
_CLIENT_DEFAULT_MODELS: dict[str, str] = {
    "codex":  "",
    "claude": "claude-sonnet-4-6",
    "gemini": "",
}

_RATE_LIMIT_MARKERS = ("429", "rate limit", "resource_exhausted", "capacity exhausted", "rateLimitExceeded", "MODEL_CAPACITY_EXHAUSTED")

def _is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m.lower() in text for m in _RATE_LIMIT_MARKERS)


class _BlockedError(Exception):
    """Agent signaled a blocker or produced no changes — needs human attention."""


def _read_worker_result(path: str) -> dict | None:
    try:
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to read worker result file {}: {}", path, e)
        return None


def _extract_pr_url(output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("https://github.com/") and "/pull/" in line:
            return line
    return None


def _default_agent_instructions() -> str:
    return """

---
## Worker Instructions

After completing your implementation:

1. If you encounter a blocker (missing API key, external dependency, decision needed from owner):
   Write `.worker_result.json` in the repo root:
   ```json
   {"type": "blocked", "blockers": ["description of what is needed"], "summary": "brief explanation"}
   ```
   Then stop — do NOT make code changes when blocked.

2. If implementation completes successfully, no action needed.
   The worker will detect your committed/uncommitted changes automatically.

Do NOT write `.worker_result.json` on success.
"""


def _build_agent_instructions(project: dict) -> str:
    configured_prompt = project.get("system_instructions")
    if isinstance(configured_prompt, str) and configured_prompt.strip():
        return configured_prompt.strip() + "\n"
    return _default_agent_instructions()


class WorkerEngine:
    def __init__(self):
        self.is_running = False
        self.thread = None

    def start(self):
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("Worker engine started worker_name={}", WORKER_NAME)

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join()
        logger.info("Worker engine stopped")

    def _run_loop(self):
        while self.is_running:
            self._print_heartbeat()
            self._process_next_job()
            time.sleep(settings.poll_interval_seconds)

    def _print_heartbeat(self):
        db = SessionLocal()
        try:
            queued = db.query(Job).filter(Job.status == "queued").count()
            running = db.query(Job).filter(Job.status == "in_progress").count()
            logger.info("[heartbeat] queued={} running={}", queued, running)
        finally:
            db.close()

    def _process_next_job(self):
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            job = (
                db.query(Job)
                .filter(Job.status == "queued")
                .filter((Job.retry_after == None) | (Job.retry_after <= now))
                .order_by(Job.created_at.asc())
                .first()
            )
            if not job:
                return

            logger.info("[job:{}] claimed project={} target_branch={} attempt={}", job.id, job.project_id, job.target_branch, job.retry_count + 1)
            job.status = "in_progress"
            job.worker_id = WORKER_NAME
            job.started_at = datetime.now(timezone.utc)
            job.retry_after = None
            job.phase = "claimed"
            db.commit()

            try:
                self._execute_job(job, db)
                job.status = "success"
                job.phase = "done"
                self._append_log(job, db, "\nJob completed successfully.")
            except _BlockedError as e:
                db.refresh(job)
                if job.status == "cancelled":
                    logger.info("[job:{}] was cancelled mid-run, leaving as cancelled", job.id)
                    return
                job.status = "blocked"
                job.phase = "done"
                self._append_log(job, db, f"\nJob blocked: {str(e)}")
            except Exception as e:
                db.refresh(job)
                if job.status == "cancelled":
                    logger.info("[job:{}] was cancelled mid-run, leaving as cancelled", job.id)
                    return
                if _is_rate_limit_error(e) and job.retry_count < settings.job_max_retries:
                    job.retry_count += 1
                    retry_at = datetime.now(timezone.utc) + timedelta(minutes=settings.job_retry_delay_minutes)
                    job.status = "queued"
                    job.phase = None
                    job.retry_after = retry_at
                    db.commit()
                    logger.info(
                        "[job:{}] rate-limit hit — retry {}/{} scheduled at {}",
                        job.id, job.retry_count, settings.job_max_retries, retry_at.strftime("%H:%M:%SZ"),
                    )
                    return
                job.status = "failed"
                job.phase = "done"
                if not job.result:
                    job.result = {"type": "failed", "error": str(e), "error_type": type(e).__name__}
                self._append_log(job, db, f"\nJob failed: {str(e)}")
            finally:
                if job.status not in ("queued", "cancelled"):
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.info("[job:{}] done status={}", job.id, job.status)
                    if job.callback_url:
                        self._send_callback(job)
        finally:
            db.close()

    def _set_phase(self, job: Job, db, phase: str):
        job.phase = phase
        db.commit()
        logger.info("[job:{}] phase={}", job.id, phase)

    def _append_log(self, job: Job, db, message: str):
        logger.info(message)
        job.logs = (job.logs or "") + message + "\n"
        db.commit()

    def _execute_job(self, job: Job, db):
        projects = load_project_configs()
        project = projects.get(job.project_id)
        if not project:
            raise ValueError(f"Project configuration for '{job.project_id}' not found.")

        repo_url = project.get("repository_url")
        if not repo_url:
            raise ValueError("repository_url not defined in project config")

        # Build environment
        env = os.environ.copy()
        if "env_vars" in project:
            env.update(project["env_vars"])
        if job.env_vars_override:
            env.update(job.env_vars_override)

        github_token = env.get("GITHUB_TOKEN")
        secrets = [s for s in [github_token] if s]

        # Inject token into URL for git operations (never logged — redacted via secrets)
        if github_token and repo_url.startswith("https://github.com/"):
            authed_url = repo_url.replace("https://github.com/", f"https://oauth2:{github_token}@github.com/")
        else:
            authed_url = repo_url

        target_branch = job.target_branch or "main"
        job_branch = f"worker/{job.id[:8]}"

        from src.config import _resolve_dir
        workspaces_dir = _resolve_dir(settings.workspaces_dir)
        workspace_dir = os.path.join(workspaces_dir, job.project_id)
        os.makedirs(workspaces_dir, exist_ok=True)

        # Resume checkpoint: if PR already submitted, skip straight to success
        if isinstance(job.result, dict) and job.result.get("pr_url"):
            pr_url = job.result["pr_url"]
            self._append_log(job, db, f"Resuming: PR already exists at {pr_url} — marking success.")
            return

        commands_ran: list[str] = []

        # 1. Sync workspace
        self._set_phase(job, db, "syncing")
        if os.path.exists(os.path.join(workspace_dir, ".git")):
            self._append_log(job, db, f"Fetching latest from {target_branch}...")
            self._run_cmd(["git", "remote", "set-url", "origin", authed_url], cwd=workspace_dir, job=job, db=db, secrets=secrets, commands_ran=commands_ran)
            self._run_cmd(["git", "fetch", "origin"], cwd=workspace_dir, job=job, db=db, secrets=secrets, commands_ran=commands_ran)
            self._run_cmd(["git", "checkout", target_branch], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
            self._run_cmd(["git", "pull"], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
        else:
            self._append_log(job, db, f"Cloning repository into {workspace_dir}...")
            self._run_cmd(["git", "clone", "--branch", target_branch, authed_url, workspace_dir], cwd=".", job=job, db=db, secrets=secrets, commands_ran=commands_ran)

        self._run_cmd(["git", "config", "user.name", settings.git_user_name], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
        self._run_cmd(["git", "config", "user.email", settings.git_user_email], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
        self._run_cmd(["git", "checkout", "-B", job_branch], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
        job.branch_name = job_branch
        db.commit()

        # Clean any stale worker result file from previous run
        result_file_path = os.path.join(workspace_dir, _WORKER_RESULT_FILE)
        if os.path.exists(result_file_path):
            os.remove(result_file_path)

        pre_head = self._run_cmd(["git", "rev-parse", "HEAD"], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran).strip()

        # 2. Execute CLI coding agent
        self._set_phase(job, db, "executing")
        cli_client = project.get("cli_client", "codex")
        cli_model = project.get("cli_model") or _CLIENT_DEFAULT_MODELS.get(cli_client, "")
        cli_effort = project.get("cli_effort", "")
        cli_flags = project.get("cli_flags", "")

        prd_file_path = os.path.join(workspace_dir, "PRD.md")
        with open(prd_file_path, "w") as f:
            f.write(job.prd_content)
            f.write("\n")
            f.write(_build_agent_instructions(project))

        cmd_parts = [cli_client]
        cmd_parts.extend(_CLIENT_AUTO_FLAGS.get(cli_client, []))
        if cli_model:
            cmd_parts.extend(["--model", cli_model])
        if cli_client == "codex" and cli_effort:
            cmd_parts.extend(["--effort", cli_effort])
        if cli_flags:
            cmd_parts.extend(shlex.split(cli_flags))
        cmd_parts.append("Please implement the requirements in PRD.md. Make sure to test your code.")

        self._append_log(job, db, f"Running: {' '.join(cmd_parts)}")
        try:
            self._run_cmd(cmd_parts, cwd=workspace_dir, job=job, db=db, env=env, commands_ran=commands_ran)
        except Exception as e:
            self._append_log(job, db, f"Agent execution failed: {str(e)}")
            raise

        # 3. Read agent result file — agent writes this on blocker
        worker_result = _read_worker_result(result_file_path)
        if worker_result:
            result_type = worker_result.get("type", "")
            if result_type == "blocked":
                blockers = worker_result.get("blockers", [])
                summary = worker_result.get("summary", "Agent signaled blocker.")
                job.result = {"type": "blocked", "blockers": blockers, "summary": summary, "commands_ran": commands_ran}
                db.commit()
                raise _BlockedError(f"Agent signaled blocker: {'; '.join(blockers) if blockers else summary}")

        # 4. No-changes detection
        post_head = self._run_cmd(["git", "rev-parse", "HEAD"], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran).strip()
        status_output = self._run_cmd(["git", "status", "--porcelain"], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
        has_uncommitted = bool(status_output.strip())
        has_committed = pre_head != post_head

        if not has_uncommitted and not has_committed:
            job.result = {
                "type": "blocked",
                "blockers": ["Agent produced no file changes"],
                "summary": "No changes were made to the repository.",
                "commands_ran": commands_ran,
            }
            db.commit()
            raise _BlockedError("Agent produced no file changes")

        # 5. Commit
        self._set_phase(job, db, "committing")
        if has_uncommitted:
            self._run_cmd(["git", "add", "."], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
            # Unstage worker-owned files that must never be committed to any target repo.
            # Use subprocess directly (check=False) so missing files don't raise.
            for _wf in ("PRD.md", ".devjob_tracker.md"):
                subprocess.run(["git", "restore", "--staged", _wf], cwd=workspace_dir, capture_output=True)
            diffstat = self._run_cmd(["git", "diff", "--cached", "--stat"], cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran)
            self._run_cmd(
                ["git", "commit", "-m", f"devworker job {job.id[:8]}: implement PRD"],
                cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran,
            )
        else:
            diffstat = self._run_cmd(
                ["git", "show", "--stat", "--pretty=format:", "HEAD"],
                cwd=workspace_dir, job=job, db=db, commands_ran=commands_ran,
            )

        # 6. Push
        self._set_phase(job, db, "pushing")
        push_env = {**env}
        if github_token:
            push_env["GH_TOKEN"] = github_token
        self._run_cmd(
            ["git", "push", "-u", "origin", job_branch],
            cwd=workspace_dir, job=job, db=db, env=push_env, secrets=secrets, commands_ran=commands_ran,
        )

        # 7. PR creation (best-effort)
        self._set_phase(job, db, "creating_pr")
        pr_url = None
        if shutil.which("gh"):
            try:
                pr_body = (
                    f"{job.prd_content.strip()}\n\n"
                    f"---\n"
                    f"**Job:** `{job.id}` | **Project:** `{job.project_id}` | **Branch:** `{job_branch}`\n\n"
                    f"<details><summary>Diff stat</summary>\n\n```\n{diffstat.strip()}\n```\n</details>"
                )
                pr_output = self._run_cmd(
                    [
                        "gh", "pr", "create",
                        "--base", target_branch,
                        "--head", job_branch,
                        "--title", f"[Worker] {job.prd_content.splitlines()[0].lstrip('# ').strip()[:72]}",
                        "--body", pr_body,
                    ],
                    cwd=workspace_dir, job=job, db=db, env=push_env, commands_ran=commands_ran,
                )
                pr_url = _extract_pr_url(pr_output)
            except Exception as e:
                self._append_log(job, db, f"PR creation failed (non-fatal): {e}")
        else:
            self._append_log(job, db, "gh CLI not found — skipping PR creation.")

        # 8. Persist structured result
        job.result = {
            "type": "success",
            "pr_url": pr_url,
            "branch": job_branch,
            "diffstat": diffstat.strip() if diffstat else "",
            "commands_ran": commands_ran,
            "summary": f"Job {job.id[:8]} completed — branch {job_branch}" + (f", PR: {pr_url}" if pr_url else ""),
        }
        db.commit()

    def _run_cmd(self, cmd, cwd, job, db, env=None, secrets=None, commands_ran: list | None = None):
        _secrets = [s for s in (secrets or []) if s]

        def _redact(text: str) -> str:
            text = _ANSI_RE.sub("", text)
            for s in _secrets:
                text = text.replace(s, "***")
            return text

        safe_preview = _redact(" ".join(str(c) for c in cmd))
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
            preexec_fn=os.setsid,  # new process group for clean abort
        )

        line_q: queue.Queue[str | None] = queue.Queue()

        def _reader():
            for line in proc.stdout:
                line_q.put(line)
            line_q.put(None)

        threading.Thread(target=_reader, daemon=True).start()

        output_parts: list[str] = []
        last_cancel_check = time.time()

        while True:
            try:
                line = line_q.get(timeout=0.2)
            except queue.Empty:
                # Check for cancellation every 5 seconds
                if time.time() - last_cancel_check >= 5:
                    last_cancel_check = time.time()
                    db.refresh(job)
                    if job.status == "cancelled":
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
            output_parts.append(safe_line + "\n")
            logger.info(safe_line)

        return_code = proc.wait()
        output = "".join(output_parts)

        if output.strip():
            job.logs = (job.logs or "") + output
            db.commit()

        if return_code != 0:
            error_msg = f"Command failed (exit {return_code}): {safe_preview}"
            job.logs = (job.logs or "") + error_msg + "\n"
            db.commit()
            raise RuntimeError(error_msg)

        return output

    def _send_callback(self, job: Job):
        try:
            payload = {
                "id": job.id,
                "project_id": job.project_id,
                "status": job.status,
                "phase": job.phase,
                "worker_id": job.worker_id,
                "branch_name": job.branch_name,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "result": job.result,
            }
            httpx.post(job.callback_url, json=payload, timeout=5.0)
            logger.info("Sent callback to {}", job.callback_url)
        except Exception as e:
            logger.error("Failed to send callback for job {}: {}", job.id, e)


# Global instance
worker_engine = WorkerEngine()
