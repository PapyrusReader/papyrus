#!/usr/bin/env bash

set -euo pipefail

if [[ "${1:-}" != "RESET" ]]; then
  echo "Reset cancelled. Type RESET exactly in the VS Code prompt."
  exit 1
fi

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_dir="${workspace_root}/server"

if pgrep -af "flutter_tools.snapshot run.*--web-hostname papyrus.localhost" >/dev/null; then
  echo "Stop the VS Code Client task before resetting project data."
  exit 1
fi

if pgrep -af "uvicorn papyrus.main:app" >/dev/null; then
  echo "Stop the VS Code Back-end task before resetting project data."
  exit 1
fi

echo "Removing server databases and PowerSync storage..."
docker compose --project-directory "${server_dir}" down --volumes --remove-orphans

media_root=".media"
if [[ -f "${server_dir}/.env" ]]; then
  configured_media_root="$(sed -n 's/^MEDIA_STORAGE_ROOT=//p' "${server_dir}/.env" | tail -n 1)"
  if [[ -n "${configured_media_root}" ]]; then
    media_root="${configured_media_root}"
  fi
fi

if [[ "${media_root}" != /* ]]; then
  media_root="${server_dir}/${media_root}"
fi

case "${media_root}" in
  /|"${HOME}"|"${workspace_root}"|"${server_dir}")
    echo "Refusing to remove unsafe media path: ${media_root}"
    exit 1
    ;;
esac

echo "Removing server media at ${media_root}..."
rm -rf -- "${media_root}"

echo "Removing Flutter Chrome profiles containing papyrus.localhost data..."
while IFS= read -r -d '' profile_dir; do
  if find "${profile_dir}/Default/IndexedDB" -maxdepth 1 -iname '*papyrus.localhost*' -print -quit 2>/dev/null | grep -q .; then
    echo "  ${profile_dir}"
    rm -rf -- "${profile_dir}"
  fi
done < <(find "${TMPDIR:-/tmp}" -mindepth 2 -maxdepth 2 -type d -path '*/flutter_tools.*/flutter_tools_chrome_device.*' -print0 2>/dev/null)

echo "Recreating empty databases and applying migrations..."
(
  cd "${server_dir}"
  docker compose up -d --wait database powersync-storage mailpit
  uv run alembic upgrade head
  ./scripts/setup_local_powersync.sh
  docker compose up -d --wait powersync
)

echo
echo "Papyrus local data reset complete."
echo "The database, uploaded media, PowerSync server state, Flutter browser IndexedDB, OPFS, and SharedPreferences are clean."
echo "Start the Back-end and Client tasks normally."
