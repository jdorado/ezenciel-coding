"""Worker engine tests.
Last edited: 2026-02-25 (QA evidence tracker parsing and PR body coverage)
"""
from __future__ import annotations

import os
import shutil
from unittest.mock import call

import pytest

from src.database.repository import SQLiteJobRepository
from src.database.session import SessionLocal, engine
from src.models.job import Base, Job
from src.worker.engine import (
    WorkerEngine,
    _build_agent_instructions,
    _build_pr_body,
    _extract_codex_agent_message_line,
    _extract_codex_error_line,
    _extract_markdown_section,
    _build_agent_command,
    _extract_codex_reasoning_line,
    _is_codex_json_command,
    _load_qa_evidence_from_tracker,
)


Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def setup_teardown():
    os.makedirs("projects/test_dummy", exist_ok=True)
    with open("projects/test_dummy/config.yaml", "w", encoding="utf-8") as handle:
        handle.write('repository_url: "dummy"\ncli_client: "codex"\n')

    yield

    if os.path.exists("projects/test_dummy"):
        shutil.rmtree("projects/test_dummy")

    db = SessionLocal()
    db.query(Job).delete()
    db.commit()
    db.close()

    if os.path.exists("workspaces/test_dummy"):
        shutil.rmtree("workspaces/test_dummy")


def test_engine_process_jobs_skips_if_none() -> None:
    worker = WorkerEngine(repository=SQLiteJobRepository())
    worker._process_next_job()


def test_engine_process_job_success_with_mock_execution(mocker) -> None:
    db = SessionLocal()
    db.add(Job(id="cmd-1", project_id="test_dummy", prd_content="Hello PRD", status="queued"))
    db.commit()
    db.close()

    worker = WorkerEngine(repository=SQLiteJobRepository())
    mocker.patch.object(worker, "_execute_job", return_value=None)

    worker._process_next_job()

    db = SessionLocal()
    updated_job = db.query(Job).filter_by(id="cmd-1").first()
    assert updated_job is not None
    assert updated_job.status == "success"
    assert updated_job.phase == "done"
    assert updated_job.completed_at is not None
    db.close()


def test_sync_workspace_existing_repo_cleans_before_checkout(tmp_path, mocker) -> None:
    worker = WorkerEngine(repository=SQLiteJobRepository())
    workspace_dir = tmp_path / "repo"
    (workspace_dir / ".git").mkdir(parents=True, exist_ok=True)

    run_cmd = mocker.patch.object(worker, "_run_cmd", return_value="")
    mocker.patch.object(worker, "_append_log")
    commands_ran: list[str] = []

    worker._sync_workspace(
        job_id="sync-1",
        workspace_dir=str(workspace_dir),
        target_branch="main",
        authed_url="https://github.com/acme/repo",
        secrets=["token-123"],
        commands_ran=commands_ran,
    )

    assert run_cmd.call_args_list == [
        call(
            ["git", "remote", "set-url", "origin", "https://github.com/acme/repo"],
            cwd=str(workspace_dir),
            job_id="sync-1",
            secrets=["token-123"],
            commands_ran=commands_ran,
        ),
        call(["git", "fetch", "origin"], cwd=str(workspace_dir), job_id="sync-1", commands_ran=commands_ran),
        call(["git", "reset", "--hard", "HEAD"], cwd=str(workspace_dir), job_id="sync-1", commands_ran=commands_ran),
        call(["git", "clean", "-fd"], cwd=str(workspace_dir), job_id="sync-1", commands_ran=commands_ran),
        call(
            ["git", "checkout", "-B", "main", "origin/main"],
            cwd=str(workspace_dir),
            job_id="sync-1",
            commands_ran=commands_ran,
        ),
    ]


def test_sync_workspace_clones_when_workspace_missing(tmp_path, mocker) -> None:
    worker = WorkerEngine(repository=SQLiteJobRepository())
    workspace_dir = tmp_path / "repo-clone"

    run_cmd = mocker.patch.object(worker, "_run_cmd", return_value="")
    mocker.patch.object(worker, "_append_log")
    commands_ran: list[str] = []

    worker._sync_workspace(
        job_id="sync-2",
        workspace_dir=str(workspace_dir),
        target_branch="dev",
        authed_url="https://github.com/acme/repo",
        secrets=["token-456"],
        commands_ran=commands_ran,
    )

    run_cmd.assert_called_once_with(
        ["git", "clone", "--branch", "dev", "https://github.com/acme/repo", str(workspace_dir)],
        cwd=".",
        job_id="sync-2",
        secrets=["token-456"],
        commands_ran=commands_ran,
    )


def test_build_agent_command_codex_uses_exec_and_ignores_effort() -> None:
    warnings: list[str] = []
    cmd = _build_agent_command(
        cli_client="codex",
        cli_model="gpt-4o",
        cli_effort="high",
        cli_flags="--json",
        on_warning=warnings.append,
    )

    assert cmd[:5] == ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--model", "gpt-4o"]
    assert "--effort" not in cmd
    assert "--json" in cmd
    assert cmd[-1] == "Please implement the requirements in PRD.md. Make sure to test your code."
    assert warnings == ["cli_effort is ignored for codex exec mode; set raw codex overrides in cli_flags if needed."]


def test_build_agent_command_non_codex_does_not_add_exec() -> None:
    cmd = _build_agent_command(
        cli_client="claude",
        cli_model="claude-sonnet-4-6",
        cli_effort="",
        cli_flags="",
    )

    assert cmd[:4] == ["claude", "-p", "--dangerously-skip-permissions", "--model"]
    assert "exec" not in cmd


def test_build_agent_command_codex_adds_json_when_not_in_flags() -> None:
    cmd = _build_agent_command(
        cli_client="codex",
        cli_model="",
        cli_effort="",
        cli_flags="--color never",
    )

    assert cmd[0:3] == ["codex", "exec", "--json"]
    assert cmd.count("--json") == 1


def test_build_agent_instructions_appends_worker_runtime_contract() -> None:
    instructions = _build_agent_instructions({"system_instructions": "Custom project prompt"})

    assert instructions.startswith("Custom project prompt")
    assert "## Worker Instructions" in instructions
    assert "## QA Evidence" in instructions


def test_build_agent_instructions_uses_worker_runtime_contract_when_empty() -> None:
    instructions = _build_agent_instructions({})

    assert "## Worker Instructions" in instructions
    assert "## QA Evidence" in instructions


def test_extract_codex_reasoning_line_reads_reasoning_event() -> None:
    raw = '{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":"plan then implement"}}'
    assert _extract_codex_reasoning_line(raw) == "plan then implement"


def test_extract_codex_reasoning_line_ignores_non_reasoning() -> None:
    raw = '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"done"}}'
    assert _extract_codex_reasoning_line(raw) is None


def test_extract_codex_reasoning_line_reads_text_list_chunks() -> None:
    raw = '{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":[{"type":"output_text","text":"step 1"},{"type":"output_text","text":"step 2"}]}}'
    assert _extract_codex_reasoning_line(raw) == "step 1\nstep 2"


def test_extract_codex_agent_message_line_reads_agent_message() -> None:
    raw = '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"done"}}'
    assert _extract_codex_agent_message_line(raw) == "done"


def test_extract_codex_error_line_reads_top_level_error() -> None:
    raw = '{"type":"error","message":"model unavailable"}'
    assert _extract_codex_error_line(raw) == "model unavailable"


def test_extract_codex_error_line_reads_item_error() -> None:
    raw = '{"type":"item.completed","item":{"id":"item_0","type":"error","message":"bad auth"}}'
    assert _extract_codex_error_line(raw) == "bad auth"


def test_is_codex_json_command_only_for_codex_exec_json() -> None:
    assert _is_codex_json_command(["codex", "exec", "--json", "x"]) is True
    assert _is_codex_json_command(["codex", "exec", "x"]) is False
    assert _is_codex_json_command(["claude", "-p", "x"]) is False


def test_extract_markdown_section_returns_body_until_next_header() -> None:
    markdown = (
        "# Title\n\n"
        "## QA Evidence\n"
        "- `poetry run pytest -q` -> PASS\n"
        "- `python scripts/run_agent.py` -> PASS\n\n"
        "## Notes\n"
        "done\n"
    )

    section = _extract_markdown_section(markdown, "QA Evidence")

    assert section == "- `poetry run pytest -q` -> PASS\n- `python scripts/run_agent.py` -> PASS"


def test_load_qa_evidence_from_tracker_prefers_qa_evidence_header(tmp_path) -> None:
    tracker = tmp_path / ".devjob_tracker.md"
    tracker.write_text(
        (
            "## QA Evidence\n"
            "- `pytest -q` -> PASS\n\n"
            "## Verification Run\n"
            "- `python scripts/run_agent.py` -> PASS\n"
        ),
        encoding="utf-8",
    )

    assert _load_qa_evidence_from_tracker(str(tmp_path)) == "- `pytest -q` -> PASS"


def test_load_qa_evidence_from_tracker_falls_back_to_verification_run(tmp_path) -> None:
    tracker = tmp_path / ".devjob_tracker.md"
    tracker.write_text(
        (
            "## Plan Summary\n"
            "work\n\n"
            "## Verification Run\n"
            "- `python scripts/run_agent.py` -> PASS\n"
        ),
        encoding="utf-8",
    )

    assert _load_qa_evidence_from_tracker(str(tmp_path)) == "- `python scripts/run_agent.py` -> PASS"


def test_build_pr_body_includes_qa_evidence_section() -> None:
    body = _build_pr_body(
        prd_content="# Feature\nShip update",
        job_id="job-123",
        project_id="proj-a",
        job_branch="worker/job-123",
        diffstat=" file.py | 2 ++",
        qa_evidence="- `pytest -q` -> PASS",
    )

    assert "## QA Evidence" in body
    assert "Source: `.devjob_tracker.md`" in body
    assert "- `pytest -q` -> PASS" in body
    assert "**Job:** `job-123` | **Project:** `proj-a` | **Branch:** `worker/job-123`" in body
