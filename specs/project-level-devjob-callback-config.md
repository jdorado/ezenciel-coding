# Project-Level DevJob Callback Config

**Status:** Implemented
**Created:** 2026-02-25
**Last edited:** 2026-02-25

---

## Goal

Move callback URL/secret ownership from runtime env coupling to project registration payload so each registered project can define its own callback contract.

---

## Contract

`POST /api/v1/projects` accepts:

- `callback_url` (optional)
- `callback_secret` (optional)

`POST /api/v1/jobs` behavior:

- If request payload omits `callback_url`, job inherits project `callback_url`.
- Request `callback_url` still overrides project default.

Worker callback sender behavior:

- Reads `callback_secret` from project config by `project_id`.
- Sends `X-Webhook-Secret` only when project callback secret is configured.

---

## Files

- `src/api/main.py`
  - Added callback fields to project registration request model.
  - Persisted callback fields in project config payload.
  - Job submission now resolves callback URL from request override or project default.
- `src/worker/engine.py`
  - `_send_callback()` now sources secret from project config (not env).
- `tests/test_api.py`
  - Added coverage for callback inheritance and override behavior.
- `tests/test_engine.py`
  - Added coverage for header inclusion/omission using project callback secret.
- `scripts/register_and_submit_job.sh`
  - Project registration payload now includes callback fields from `config.yaml`.
- `README.md`
  - Updated registration examples with callback fields.

---

## Validation

- `poetry run python -m pytest -q tests/test_api.py tests/test_engine.py -k "callback or register_project_success or submit_job_success or submit_job_request_callback_url_overrides_project_default"`
  - Passed.
