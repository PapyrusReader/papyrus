#!/usr/bin/env python3
"""Production backup hook. The Mac pulls a consistent archive and acknowledges a verified restore."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SPOOL = Path("/var/lib/papyrus-release-backup")
API = "papyrus-production-api-1"
DB = "papyrus-production-database-1"
COUNTS = """SELECT json_build_object('users',(SELECT count(*) FROM users),
'books',(SELECT count(*) FROM books),'media_assets',(SELECT count(*) FROM media_assets),
'migration',(SELECT version_num FROM alembic_version))"""
DEPLOY = Path("/srv/apps/papyrus/deploy")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        json.dump(value, out)
    os.replace(out.name, path)


def database(command, *args, **kwargs):
    return run(
        "docker",
        "exec",
        DB,
        "sh",
        "-ec",
        f'exec {command} -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"',
        "sh",
        *args,
        **kwargs,
    )


def snapshot(job, request):
    info = json.loads(run("docker", "inspect", API, capture_output=True).stdout)[0]
    if not info["State"]["Running"]:
        raise RuntimeError("Production API must be running before the backup")
    media = next(m["Source"] for m in info["Mounts"] if m["Destination"] == "/var/lib/papyrus/media")
    tree = job / "contents"
    (tree / "media").mkdir(parents=True)
    (tree / "deploy").mkdir()
    sync = ["rsync", "-a", "--delete", media + "/", str(tree / "media") + "/"]
    first = subprocess.run(sync).returncode
    if first not in (0, 24):
        raise RuntimeError(f"Media pre-copy failed ({first})")

    try:
        run("docker", "stop", "--time", "30", API, stdout=subprocess.DEVNULL)
        run(sync[0], "--checksum", *sync[1:])
        with (tree / "database.dump").open("wb") as out:
            database("pg_dump", "-Fc", stdout=out)
        counts = json.loads(database("psql", "-Atc", COUNTS, capture_output=True).stdout)
        for name in ("production.env", "compose.yml", "Caddyfile"):
            shutil.copy2(DEPLOY / name, tree / "deploy" / name)
        shutil.copytree(DEPLOY / "secrets", tree / "deploy/secrets")
    finally:
        run("docker", "start", API, stdout=subprocess.DEVNULL)

    metadata = {
        "release": request,
        "counts": counts,
        "database_image": json.loads(run("docker", "inspect", DB, capture_output=True).stdout)[0]["Config"][
            "Image"
        ],
    }
    write_json(tree / "metadata.json", metadata)
    archive = job / "backup.tar"
    run("tar", "-cf", archive, "-C", tree, "metadata.json", "database.dump", "deploy", "media")
    shutil.rmtree(tree)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"id": job.name, "release": request, "sha256": digest, "bytes": archive.stat().st_size}


def hook():
    request = json.load(sys.stdin)
    if not (
        re.fullmatch(r"\d+\.\d+\.\d+", request["version"])
        and re.fullmatch(r"[a-f0-9]{40}", request["revision"])
        and re.fullmatch(r"ghcr\.io/papyrusreader/server@sha256:[a-f0-9]{64}", request["image"])
    ):
        raise ValueError("Invalid deployment request")
    job_id = uuid.uuid4().hex
    job = SPOOL / job_id
    job.mkdir(mode=0o700)
    lease = (job / "active.lock").open("x")
    try:
        fcntl.flock(lease, fcntl.LOCK_EX)
        manifest = snapshot(job, request)
        write_json(job / "ready.json", manifest)
        print("Waiting for the off-host backup and restore check", flush=True)
        deadline = time.monotonic() + 2700
        while time.monotonic() < deadline:
            receipt = job / "verified.json"
            if receipt.exists():
                result = json.loads(receipt.read_text())
                if result["id"] != job_id or result["sha256"] != manifest["sha256"]:
                    raise ValueError("Backup receipt does not match the active archive")
                write_json(SPOOL / "last-verified.json", manifest | {"snapshot": result["snapshot"]})
                print("Off-host backup and restore verified", flush=True)
                return
            time.sleep(3)
        raise TimeoutError("Mac backup worker did not verify the archive within 45 minutes")
    finally:
        lease.close()
        shutil.rmtree(job)


def pending():
    for path in sorted(SPOOL.glob("*/ready.json")):
        try:
            with path.with_name("active.lock").open() as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if not path.with_name("verified.json").exists():
                        return json.loads(path.read_text())
        except FileNotFoundError:
            continue
    return None


def main(args):
    os.umask(0o077)
    SPOOL.mkdir(mode=0o700, exist_ok=True)
    if not args:
        hook()
        return
    if args == ["pending"]:
        print(json.dumps(pending()))
        return
    if len(args) != 2 or not re.fullmatch("[0-9a-f]{32}", args[1]):
        raise ValueError("Expected stream or confirm and a backup id")
    job = SPOOL / args[1]
    manifest = json.loads((job / "ready.json").read_text())
    if args[0] == "stream":
        with (job / "backup.tar").open("rb") as source:
            shutil.copyfileobj(source, sys.stdout.buffer, 1024 * 1024)
    elif args[0] == "confirm":
        receipt = json.load(sys.stdin)
        if (
            receipt["id"] != manifest["id"]
            or receipt["sha256"] != manifest["sha256"]
            or not re.fullmatch("[a-f0-9]{64}", receipt["snapshot"])
        ):
            raise ValueError("Invalid restore receipt")
        write_json(job / "verified.json", receipt)
    else:
        raise ValueError("Unknown backup operation")


if __name__ == "__main__":

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    main(sys.argv[1:])
