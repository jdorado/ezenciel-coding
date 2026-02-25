# Remove DevJob Callback Support

**Status:** Implemented
**Created:** 2026-02-25
**Last edited:** 2026-02-25

---

## Goal

Remove callback delivery from `ezenciel-coding` so job completion is no longer tied to outbound webhook posting. `stocks` now uses poll-based completion handling, so worker callback support is redundant and can be removed.

---

## Scope

- API contracts:
  - Remove callback fields from project registration and job payload/response models.
- Worker engine:
  - Remove callback send invocation and callback sender implementation.
- Persistence:
  - Remove callback URL field from repository serialization/defaults and SQLAlchemy model.
- Tooling/docs/tests:
  - Remove callback fields from registration helper script.
  - Remove callback references from README examples.
  - Update tests that assert callback behavior.

---

## Checklist

- [x] Remove callback fields from `src/api/main.py` request/response models and submit flow.
- [x] Remove callback send path from `src/worker/engine.py`.
- [x] Remove callback field from `src/database/repository.py` and `src/models/job.py`.
- [x] Remove callback fields from `scripts/register_and_submit_job.sh`.
- [x] Update README examples to remove callback configuration.
- [x] Replace/remove callback-specific tests in `tests/test_api.py` and `tests/test_engine.py`.
- [x] Run targeted tests for API + worker.

---

## Validation

- `poetry run python -m pytest -q tests/test_api.py tests/test_engine.py`
  - Passed (22 tests) on 2026-02-25.

---

## Notes

This change is intentionally one-way for runtime behavior: callback fields are removed from active contracts and callback posting is no longer executed by the worker.
Legacy `callback_url` / `callback_secret` request fields are now rejected with `422` (`extra=forbid`) instead of being silently ignored.
