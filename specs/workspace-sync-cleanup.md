# Worker Workspace Sync Cleanup
**Created:** 2026-02-26
**Last edited:** 2026-02-26
**Status:** Implemented

## Goal
Prevent job startup failures when a reused workspace contains leftover local changes from a previous job (for example `PRD.md`), which can block `git checkout <target_branch>`.

## Problem
Observed failure during sync phase:
- `git fetch origin` succeeds
- `git checkout main` fails because local changes would be overwritten
- job stops before pull/branch creation

## Implemented
- Refactored workspace sync into `WorkerEngine._sync_workspace(...)`.
- For existing git workspaces, sync flow now:
  - `git remote set-url origin <authed_url>`
  - `git fetch origin`
  - `git reset --hard HEAD`
  - `git clean -fd`
  - `git checkout -B <target_branch> origin/<target_branch>`
- For missing workspaces, behavior stays clone-first:
  - `git clone --branch <target_branch> <authed_url> <workspace_dir>`

## Why this approach
- Explicitly removes tracked and untracked carry-over artifacts from prior jobs.
- Aligns local target branch with remote branch in one deterministic checkout command.
- Keeps worker orchestration thin while isolating sync behavior in a single method.

## Verification checklist
- [x] Existing-workspace sync path cleans stale changes before branch switch
- [x] Fresh-workspace sync path still clones target branch
- [x] Unit coverage added for both sync paths
