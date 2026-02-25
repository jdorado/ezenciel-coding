# Codex Thinking-Only Logs
<!-- Updated: 2026-02-25 -->

## Context

Worker logs for Codex jobs were too noisy because every stdout line from `codex exec` was persisted and printed (thread lifecycle events, warnings, metadata).

Requested behavior: keep only the Codex thinking stream in job logs.

## Scope

- `src/worker/engine.py`
- `tests/test_engine.py`

## Design

1. Ensure Codex runs with JSON event output:
   - default command includes `codex exec --json`
   - do not duplicate `--json` when already passed in `cli_flags`
2. During `_run_cmd`, detect Codex JSON mode and filter output:
   - persist/log only reasoning events (`item.completed` with `item.type=reasoning`)
   - prefix stored lines with `[thinking]`
3. Keep non-reasoning Codex lines out of normal logs, but keep a bounded diagnostic buffer for failure cases and append it only when command exits non-zero.

## Checklist

- [x] Add Codex JSON-mode detection helper
- [x] Add reasoning-event extraction helper
- [x] Filter Codex logs to reasoning-only in `_run_cmd`
- [x] Add tests for command builder and helpers
- [x] Run tests and confirm pass

## Verification

- Run: `poetry run python -m pytest -q tests/test_engine.py`
- Verify Codex command building includes exactly one `--json`
- Verify reasoning extraction returns text only for reasoning events
