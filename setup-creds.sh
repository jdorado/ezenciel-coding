#!/bin/bash
# Copy CLI agent credentials from host to container-ready locations
# Run this on the dev machine before docker-compose up

set -e

check_and_report() {
    local tool=$1
    local dir=$2
    if [ -d "$dir" ] && [ "$(ls -A "$dir")" ]; then
        echo "  ✓ $tool ($dir)"
    else
        echo "  ✗ $tool — not found at $dir, run: $3"
    fi
}

echo "Checking CLI agent credentials..."
check_and_report "codex"  "$HOME/.codex"  "codex login"
check_and_report "gemini" "$HOME/.gemini" "gemini login"
check_and_report "claude" "$HOME/.claude" "claude login"
echo ""
echo "docker-compose mounts these dirs into the container automatically."
