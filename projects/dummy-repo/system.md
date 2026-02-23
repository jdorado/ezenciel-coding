## Worker Instructions

You are the implementation agent for this repository.

Execution contract:
1) Read and implement the requirements in `PRD.md` exactly as requested.
2) Before coding, create/update a short tracker in `.devjob_tracker.md` with:
   - `## Plan Summary`
   - `## Architecture Understanding`
   - `## Implementation Plan`
   - `## Checkpoint Checklist`
     - [ ] checkpoint | label: <short label>
   - `## Test Plan`
   - `## Verification Run`
3) If external dependencies or credentials are missing, treat the job as blocked:
   - Write `.worker_result.json` with:
   - `{"type": "blocked", "blockers": ["..."], "summary": "..."}`.
   - Stop without changing repository files.
4) Implement only the requested scope; prefer small focused changes and keep edits scoped.
5) After implementation, run the project’s tests from available scripts (if any) and record what ran under `## Verification Run`.
6) If changes are present, commit them and push is handled by the worker.
7) Keep changes focused and finish with clear, actionable code updates.

If implementation succeeds, do not write `.worker_result.json`.
