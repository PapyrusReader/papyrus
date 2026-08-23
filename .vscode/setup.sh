#!/usr/bin/env bash

set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
client_dir="${workspace_root}/client/app"
server_dir="${workspace_root}/server"

required_commands=(git flutter docker uv openssl)

for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}"
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is required but 'docker compose' is unavailable."
  exit 1
fi

echo "Initializing workspace projects..."
git -C "${workspace_root}" submodule sync --recursive
git -C "${workspace_root}" submodule update --init --recursive

echo "Preparing client..."
if [[ ! -f "${client_dir}/.dart_defines" ]]; then
  cp "${client_dir}/.dart_defines.example" "${client_dir}/.dart_defines"
  echo "Created client/app/.dart_defines from the example."
fi

(
  cd "${client_dir}"
  flutter pub get
)

echo "Preparing server..."
if [[ ! -f "${server_dir}/.env" ]]; then
  cp "${server_dir}/.env.example" "${server_dir}/.env"
  echo "Created server/.env from the example."
fi

(
  cd "${server_dir}"
  uv sync --extra dev

  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a

  private_key_file="${POWERSYNC_JWT_PRIVATE_KEY_FILE:-.local/powersync/private.pem}"
  public_key_file="${POWERSYNC_JWT_PUBLIC_KEY_FILE:-.local/powersync/public.pem}"

  if [[ ! -f "${private_key_file}" || ! -f "${public_key_file}" ]]; then
    if [[ "${private_key_file}" != ".local/powersync/private.pem" ||
          "${public_key_file}" != ".local/powersync/public.pem" ]]; then
      echo "Configured PowerSync key files are missing. Create them before rerunning setup:"
      echo "  ${private_key_file}"
      echo "  ${public_key_file}"
      exit 1
    fi

    ./scripts/generate_dev_powersync_keys.sh
  fi

  docker compose up -d --wait database powersync-storage mailpit
  uv run alembic upgrade head
  ./scripts/setup_local_powersync.sh
)

echo
echo "Papyrus workspace setup complete."
echo "Start the Storage, Back-end, and Client VS Code tasks independently."
