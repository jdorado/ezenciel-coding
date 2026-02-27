# Deterministic Pre-Job Setup

Last edited: 2026-02-27 (add repo script convention auto-detection for pre-job setup)

## Problem

Worker jobs spend excessive time in coding-agent setup loops (dependency/env bootstrap) before touching feature scope.

## Goal

Execute repository-owned setup commands before coding LLM starts so runtime is deterministic and repeatable.

## Design

- Add generic project config fields:
  - `pre_job_setup_command`
  - `pre_job_setup_commands`
  - `pre_job_setup_timeout_seconds`
- Worker runs setup commands in workspace before `codex exec`.
- If no setup commands are configured in project registration, worker auto-detects:
  - `deploy/scripts/worker_pre_setup.sh`
  - `scripts/worker_pre_setup.sh`
  - Script must exist in the repository content at the target branch being cloned by the worker.
- If setup command fails/timeouts, mark job as blocked early with explicit setup error.
- Worker prompt includes pre-setup contract so LLM avoids re-install/bootstrap churn.

## Checklist

- [x] Worker supports project-configured pre-job setup commands.
- [x] Worker auto-runs standard repo pre-setup script when present.
- [x] Worker blocks early on setup failures.
- [x] Project registration API supports pre-job setup fields.
- [x] Engine/API tests cover pre-job setup behavior.
