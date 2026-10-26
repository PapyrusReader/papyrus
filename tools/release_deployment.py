"""Read-only production checks. Stored attestations never substitute for these probes."""

import ast
import json
import re
import shlex
import subprocess
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class DeploymentError(RuntimeError):
    pass


def validate_target(target):
    if not isinstance(target, dict) or any(not isinstance(target.get(key), str) for key in ("url", "ssh_host", "container")):
        raise DeploymentError("Configure the server URL, SSH host, and API container")

    url = urlsplit(target["url"])

    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise DeploymentError("Server URL must be an HTTPS origin without credentials, query, or fragment")

    if url.path not in ("", "/"):
        raise DeploymentError("Server URL must be an origin without a path")

    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", target["ssh_host"]):
        raise DeploymentError("Use an SSH host or configured SSH alias")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", target["container"]):
        raise DeploymentError("Use a Docker container name")


def request_json(url):
    request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "papyrus-release"})

    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except (OSError, URLError, ValueError) as error:
        raise DeploymentError(f"Cannot verify public API: {type(error).__name__}") from error


def migration_heads(sources):
    revisions = set()
    parents = set()

    for source in sources:
        values = {}

        for statement in ast.parse(source).body:
            if isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            elif isinstance(statement, ast.Assign):
                targets = statement.targets
            else:
                continue

            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                    values[target.id] = ast.literal_eval(statement.value)

        if "revision" not in values:
            continue

        revisions.add(values["revision"])
        parent = values.get("down_revision")

        if parent:
            parents.update([parent] if isinstance(parent, str) else parent)

    heads = revisions - parents

    if not heads:
        raise DeploymentError("Cannot determine the released database migration")

    return sorted(heads)


def remote(target, args):
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--",
        target["ssh_host"], shlex.join(args),
    ]

    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeploymentError("Cannot reach deployment host") from error

    if result.returncode:
        raise DeploymentError("Deployment inspection failed (check SSH access, container, and database)")

    return result.stdout


DATABASE_PROBE = """
import asyncio, json
from importlib.metadata import version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from papyrus.config import get_settings

async def inspect():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.scalars(text('SELECT version_num FROM alembic_version'))
            print(json.dumps({'version': version('papyrus-server'), 'migrations': sorted(rows)}))
    finally:
        await engine.dispose()

asyncio.run(inspect())
"""


def inspect_server(target):
    container = target["container"]
    state = json.loads(remote(target, [
        "docker", "inspect", "--format",
        '{{json .State}}', container,
    ]))

    if not isinstance(state, dict) or not state.get("Running") or (state.get("Health") or {}).get("Status") != "healthy":
        raise DeploymentError("Server container is not healthy")

    image_id = remote(target, ["docker", "inspect", "--format", "{{.Image}}", container]).strip()
    image = json.loads(remote(target, [
        "docker", "image", "inspect", "--format",
        '{"labels":{{json .Config.Labels}},"digests":{{json .RepoDigests}}}', image_id,
    ]))
    database = json.loads(remote(target, ["docker", "exec", container, "python", "-c", DATABASE_PROBE]))

    if not isinstance(image, dict) or not isinstance(database, dict):
        raise DeploymentError("Deployment inspection returned invalid data")

    current_id = remote(target, ["docker", "inspect", "--format", "{{.Image}}", container]).strip()

    if current_id != image_id:
        raise DeploymentError("Server changed during verification, retry")

    return {**image, **database}


def verify_server(target, *, version, revision, image_record, migrations):
    validate_target(target)
    origin = target["url"].rstrip("/")
    health = request_json(f"{origin}/health")

    if not isinstance(health, dict) or health.get("status") != "healthy":
        raise DeploymentError("Public API is not healthy")

    schema = request_json(f"{origin}/openapi.json")

    if not isinstance(schema, dict) or not isinstance(schema.get("info"), dict):
        raise DeploymentError("Public API returned invalid version metadata")

    actual_version = schema.get("info", {}).get("version")

    if actual_version != version:
        raise DeploymentError(f"live API is {actual_version or 'unknown'}, expected {version}")

    lines = image_record.strip().splitlines()

    if len(lines) != 2 or not re.fullmatch(r"Digest sha256:[0-9a-f]{64}", lines[1]):
        raise DeploymentError("Published image digest is missing or invalid")

    image = lines[0].rsplit(":", 1)[0]
    digest = lines[1].removeprefix("Digest ")
    observed = inspect_server(target)
    labels = observed.get("labels") or {}

    if labels.get("org.opencontainers.image.revision") != revision:
        raise DeploymentError("Running server image differs from the release commit")

    if observed.get("version") != version:
        raise DeploymentError("Running server package differs from the release version")

    if f"{image}@{digest}" not in (observed.get("digests") or []):
        raise DeploymentError("Running server image differs from the published digest")

    if observed.get("migrations") != sorted(migrations):
        raise DeploymentError("Database migration differs from the release (deploy migrations)")

    return {"revision": revision, "digest": digest, "migrations": sorted(migrations)}
