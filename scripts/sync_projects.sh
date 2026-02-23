#!/usr/bin/env bash
# Sync local projects/ (with secrets) to the VM's projects path.
# Projects are gitignored — this is the only way to get them to the VM.
# Date edited: 2026-02-23

set -euo pipefail

SOURCE_DIR="${SYNC_SOURCE_DIR:-/Users/jdorado/dev/sandbox/ezenciel-coding/projects}"
VM_HOST="${SYNC_VM_HOST:-}"
VM_USER="${SYNC_VM_USER:-github-runner}"
DEST_DIR="${SYNC_DEST_DIR:-/home/github-runner/ezenciel-projects}"
DRY_RUN="${SYNC_DRY_RUN:-0}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/sync_projects.sh [--host <host>] [--user <user>] [--dest <dir>] [--source <dir>] [--dry-run]

Options:
  --host <host>    VM hostname or IP (required if SYNC_VM_HOST not set)
  --user <user>    SSH user (default: github-runner)
  --dest <dir>     Destination path on VM (default: /home/github-runner/ezenciel-projects)
  --source <dir>   Local projects folder (default: ./projects)
  --dry-run        Show what would run without executing

Environment variables:
  SYNC_VM_HOST, SYNC_VM_USER, SYNC_DEST_DIR, SYNC_SOURCE_DIR, SYNC_DRY_RUN
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)   VM_HOST="$2";   shift 2 ;;
    --user)   VM_USER="$2";   shift 2 ;;
    --dest)   DEST_DIR="$2";  shift 2 ;;
    --source) SOURCE_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1;     shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$VM_HOST" ]]; then
  echo "Error: --host or SYNC_VM_HOST is required" >&2
  usage
  exit 1
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Error: source directory does not exist: $SOURCE_DIR" >&2
  exit 1
fi

REMOTE_CMD="mkdir -p '${DEST_DIR}' && find '${DEST_DIR}' -mindepth 1 -exec rm -rf -- {} + 2>/dev/null; tar -C '${DEST_DIR}' -xpf -"

echo "Syncing: $SOURCE_DIR -> ${VM_USER}@${VM_HOST}:${DEST_DIR}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry run — would execute:"
  echo "  tar ... | ssh ${VM_USER}@${VM_HOST} \"${REMOTE_CMD}\""
  exit 0
fi

COPYFILE_DISABLE=1 tar -C "$SOURCE_DIR" -cpf - \
  --exclude='._*' \
  --exclude='.DS_Store' \
  . | ssh "${VM_USER}@${VM_HOST}" "$REMOTE_CMD"

echo "Sync complete: ${DEST_DIR} on ${VM_HOST}"
