# Project Target Branch Single Source

Last edited: 2026-02-25

## Goal
- Ensure submitted jobs always use the registered project `target_branch` from project config storage (SQLite `projects/<id>/config.yaml` or Mongo `projects` collection).
- Remove job-level branch overrides so branch selection has one authoritative source.

## Why
- `stocks` project had `target_branch=main` in project registration data, but jobs could still submit with another branch (for example `dev`) through request payload overrides.
- This created PR base-branch mismatches and made behavior depend on caller-side env/config.

## Contract Changes
- `POST /api/v1/jobs` request body no longer accepts `target_branch`.
- Job submission resolves `target_branch` from registered project config and persists that into job records.
- Project registration payload/response includes `target_branch`.

## Implementation Notes
- Updated `src/api/main.py`:
  - Removed `target_branch` from `JobSubmitRequest`.
  - Added `target_branch` to `ProjectRegisterRequest` and `ProjectResponse`.
  - Added validation for non-empty project `target_branch`.
  - `submit_job()` now reads `target_branch` from `load_project_configs()` result and errors clearly if missing.
- Updated `scripts/register_and_submit_job.sh`:
  - Sends `target_branch` only during project registration.
  - Removed `target_branch` from job submit payload.
- Updated docs and sample project config:
  - `README.md` registration examples now include `target_branch`.
  - `projects/dummy-repo/config.yaml` now includes `target_branch: "main"`.

## Tests
- Updated `tests/test_api.py`:
  - Confirms submitted job returns `target_branch` from project config.
  - Confirms `/api/v1/jobs` rejects legacy `target_branch` override field.
  - Confirms non-default project branch (`dummy-dev -> dev`) is used.
  - Registration assertions now include `target_branch`.

## Checklist
- [x] Remove job-level target branch override from API request model
- [x] Persist and return project-level `target_branch`
- [x] Resolve submit-time target branch from registered project config
- [x] Update local registration helper script
- [x] Update docs/examples
- [x] Update API tests for new behavior
