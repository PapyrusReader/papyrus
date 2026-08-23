#!/usr/bin/env bash

set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if git -C "${workspace_root}" remote get-url origin >/dev/null 2>&1; then
  echo "Pulling workspace changes..."
  git -C "${workspace_root}" pull --ff-only
else
  echo "Workspace remote is not configured."
fi

echo "Synchronizing workspace projects to their pinned revisions..."
git -C "${workspace_root}" submodule sync --recursive
git -C "${workspace_root}" submodule update --init --recursive

echo "Papyrus workspace is up to date."
