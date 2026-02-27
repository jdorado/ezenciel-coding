# Registration System Instructions QA Contract
<!-- Updated: 2026-02-26 (generalize runtime preflight wording across projects) -->

## Context

Recent jobs passed with weak or incomplete QA evidence (for example, unit-only validation with no realistic runtime path such as `scripts/run_agent.py` / `scripts/run_tool.py`). Project registration previously persisted `system_instructions` as-is, so strict QA policy was optional and inconsistent.

Follow-up gap (2026-02-26): some runtime checks failed before code paths due missing env/service preflight, and workers treated those as implementation failures instead of setup blockers.

## Goal

Enforce a mandatory QA evidence contract at project registration time so every stored `system_instructions` payload (SQLite or MongoDB) requires:

1. realistic runtime verification before/after fix;
2. focused automated tests;
3. explicit command-level QA evidence in final report;
4. blocked state when runtime verification prerequisites are missing.
5. explicit preflight for runtime validation paths (required env/config + dependency readiness when applicable).

## Scope

- `src/api/main.py`
- `tests/test_api.py`

## Design

1. Introduce a registration-time QA contract constant in API layer.
2. Add a composer helper that:
   - appends the QA contract to provided `system_instructions`;
   - injects the QA contract when `system_instructions` is omitted;
   - avoids duplicate append if already present.
3. Persist composed instructions through existing project registration paths (SQLite and MongoDB).
4. For SQLite, always write `system.md` from the composed payload.
5. Extend API tests to verify:
   - custom instructions preserve user text and include QA contract;
   - missing instructions receive injected QA contract.
6. Update QA contract text to force generic runtime preflight checks:
   - read env/config contracts from repository-owned docs/files;
   - map equivalent variable names when repository conventions differ from worker config;
   - verify required dependent services are configured and reachable before classifying failures as code regressions.

## Checklist

- [x] Add enforced QA contract composer to registration payload builder
- [x] Ensure persisted config contains composed `system_instructions`
- [x] Ensure `system.md` write path uses persisted composed payload
- [x] Add/adjust tests for custom and default instruction registration
- [x] Run test verification and capture results
- [x] Add explicit runtime preflight guidance to registration QA contract

## Verification

- Command: `poetry run python -m pytest -q tests/test_api.py`
- Result: `13 passed`
- Command: `poetry run python -m pytest -q tests/test_engine.py`
- Result: `14 passed`
