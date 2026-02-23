#!/usr/bin/env bash
# Sync only CLI agent auth files to the VM (not entire dirs).
# Date edited: 2026-02-23

set -euo pipefail

VM_NAME="${SYNC_VM_NAME:-}"
VM_ZONE="${SYNC_VM_ZONE:-us-central1-a}"
VM_HOST="${SYNC_VM_HOST:-}"
VM_USER="${SYNC_VM_USER:-github-runner}"
DEST_ROOT="${SYNC_DEST_ROOT:-/home/$VM_USER}"
DRY_RUN="${SYNC_DRY_RUN:-0}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/sync_creds.sh --vm <name>|--host <host> [options]

Options:
  --vm <name>        GCP VM instance name (uses gcloud compute ssh)
  --host <host>      VM hostname or IP (plain SSH)
  --zone <zone>      GCP zone (default: us-central1-a)
  --user <user>      SSH user (default: github-runner)
  --dest-root <dir>  Sync destination root on VM (default: /home/<user>, e.g. /home/github-runner)
  --dry-run

Environment: SYNC_VM_NAME, SYNC_VM_ZONE, SYNC_VM_HOST, SYNC_VM_USER, SYNC_DEST_ROOT, SYNC_DRY_RUN
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm)        VM_NAME="$2";   shift 2 ;;
    --zone)      VM_ZONE="$2";   shift 2 ;;
    --host)      VM_HOST="$2";   shift 2 ;;
    --user)      VM_USER="$2";   shift 2 ;;
    --dest-root) DEST_ROOT="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1;      shift ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$VM_NAME" && -z "$VM_HOST" ]]; then
  echo "Error: --vm or --host is required" >&2
  usage; exit 1
fi

pipe_remote() {
  if [[ -n "$VM_NAME" ]]; then
    gcloud compute ssh "$VM_NAME" --zone "$VM_ZONE" --command "$1"
  else
    ssh "${VM_USER}@${VM_HOST}" "$1"
  fi
}

# Sync a list of files from a source dir via tar pipe — content never touches command args
sync_files() {
  local src_dir="$1"
  local dest_dir="$2"
  shift 2
  local files=("$@")

  local existing=()
  for f in "${files[@]}"; do
    [[ -f "${src_dir}/${f}" ]] && existing+=("$f") || echo "  Skipping ${src_dir}/${f} (not found)"
  done
  [[ ${#existing[@]} -eq 0 ]] && return

  echo "  ${src_dir}/{${existing[*]}} -> ${dest_dir}/"
  [[ "$DRY_RUN" == "1" ]] && return

  COPYFILE_DISABLE=1 tar -C "$src_dir" -cpf - --no-xattrs "${existing[@]}" | \
    pipe_remote "sudo mkdir -p '${dest_dir}' && sudo tar -C '${dest_dir}' -xpf - && sudo chown -R '${VM_USER}' '${dest_dir}' && sudo find '${dest_dir}' -maxdepth 1 -type f -exec chmod 600 {} +"
}

echo "==> codex"
sync_files "$HOME/.codex"  "${DEST_ROOT}/.codex"  auth.json

echo "==> gemini"
sync_files "$HOME/.gemini" "${DEST_ROOT}/.gemini" google_accounts.json oauth_creds.json settings.json

echo "==> claude"
sync_files "$HOME/.claude" "${DEST_ROOT}/.claude" settings.json
sync_files "$HOME" "${DEST_ROOT}" .claude.json

echo "Credentials sync complete."
