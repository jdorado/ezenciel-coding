# Codex Non-Interactive Worker Init Fix
<!-- Updated: 2026-02-25 -->

## Context

Worker jobs for projects using `cli_client: codex` failed during execution phase with:

- `Error: stdin is not a terminal`

Observed command:

- `codex --dangerously-bypass-approvals-and-sandbox "Please implement..."`

Root cause: top-level `codex` launches interactive TUI mode, which requires a TTY. The worker runs subprocesses with pipes (non-TTY), so job init fails before implementation begins.

## Scope

- `src/worker/engine.py`
- `tests/test_engine.py`

## Design

1. Add a command builder for CLI execution.
2. For codex only, force non-interactive mode by inserting `exec`:
   - `codex exec ...`
3. Keep current behavior for other clients (`claude`, `gemini`).
4. Do not pass `--effort` to `codex exec` (unsupported by current Codex CLI parsing); emit a warning into job logs when `cli_effort` is set.

## Checklist

- [x] Reproduce root cause from logs (`stdin is not a terminal` on top-level codex call)
- [x] Implement codex-specific non-interactive command path
- [x] Preserve existing auto-flags and prompt wiring
- [x] Add tests for command builder behavior
- [x] Run test suite subset and confirm pass

## Verification Plan

- Run: `poetry run python -m pytest -q tests/test_engine.py`
- Confirm new tests validate:
  - codex command starts with `codex exec`
  - no `--effort` injected
  - non-codex clients unchanged
