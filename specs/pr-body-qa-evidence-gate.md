# PR Body QA Evidence Gate
<!-- Updated: 2026-02-25 -->

## Context

Worker-created GitHub PRs currently include PRD text and diff stat, but not explicit QA evidence produced during implementation. This reduces review confidence and allows jobs to complete without visible command-level verification evidence in the PR.

## Goal

Require QA evidence before job completion and include that evidence directly in the generated GitHub PR body.

## Scope

- `src/worker/engine.py`
- `tests/test_engine.py`

## Design

1. Add markdown section parsing helpers to extract evidence from `.devjob_tracker.md`.
2. Resolve QA evidence from tracker with priority:
- `## QA Evidence`
- fallback `## Verification Run`
3. Enforce a QA evidence gate after agent execution:
- if evidence is missing, mark job `blocked` with clear remediation context.
4. Build PR body with an explicit `## QA Evidence` section sourced from tracker content.
5. Add unit tests covering section extraction, tracker fallback behavior, and PR body composition.

## Checklist

- [x] Add tracker markdown extraction helper(s)
- [x] Add QA evidence loader from `.devjob_tracker.md`
- [x] Gate job completion when QA evidence is missing
- [x] Include QA evidence in generated PR body
- [x] Add test coverage for helper and PR body behavior
- [x] Run verification tests and record outputs

## Verification

- Command: `poetry run python -m pytest -q tests/test_engine.py`
- Result: `20 passed`
