#!/usr/bin/env python3
"""Pull a pending Papyrus release backup into encrypted local storage and verify a restore."""

import fcntl
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

DESTINATION = Path.home() / "Backups/papyrus"
REPOSITORY = DESTINATION / "release.restic"
PASSWORD = Path.home() / ".config/papyrus-backup-keys/release-restic.password"
HOST = [
    "ssh",
    "-T",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "ServerAliveInterval=20",
    "-o",
    "ServerAliveCountMax=3",
    "hetzner",
    "/etc/papyrus-release/backup-check",
]
RESTIC = ["restic", "--repo", str(REPOSITORY), "--password-file", str(PASSWORD)]
COUNTS = """SELECT json_build_object('users',(SELECT count(*) FROM users),
'books',(SELECT count(*) FROM books),'media_assets',(SELECT count(*) FROM media_assets),
'migration',(SELECT version_num FROM alembic_version))"""


def init_repository():
    os.umask(0o077)
    DESTINATION.mkdir(mode=0o700, parents=True, exist_ok=True)
    PASSWORD.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if REPOSITORY.exists():
        subprocess.run([*RESTIC, "cat", "config"], stdout=subprocess.DEVNULL, check=True)
        return
    if not PASSWORD.exists():
        with PASSWORD.open("x") as out:
            out.write(secrets.token_urlsafe(48) + "\n")
    subprocess.run([*RESTIC, "init"], check=True)


def install_agent():
    init_repository()
    home = Path.home()
    directory = home / "Library/Application Support/Papyrus/ReleaseBackup"
    logs = home / "Library/Logs/Papyrus"
    agents = home / "Library/LaunchAgents"
    for path in (directory, logs, agents):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    worker = directory / Path(__file__).name
    if worker.resolve() != Path(__file__).resolve():
        shutil.copy2(__file__, worker)
    label = "com.papyrus-reader.release-backup"
    plist = agents / (label + ".plist")
    domain = f"gui/{os.getuid()}"
    config = {
        "Label": label,
        "ProgramArguments": ["/usr/bin/caffeinate", "-i", sys.executable, str(worker)],
        "StartInterval": 120,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": str(logs / "release-backup.log"),
        "StandardErrorPath": str(logs / "release-backup-errors.log"),
    }
    with plist.open("wb") as out:
        plistlib.dump(config, out)
    service = f"{domain}/{label}"
    if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, plist], check=True)


def restore_database(archive, metadata, job_id):
    image = metadata["database_image"]
    if not re.fullmatch(r"postgres:[0-9]+-alpine", image):
        raise ValueError("Unexpected PostgreSQL image")
    name = "papyrus-restore-" + job_id + "-" + secrets.token_hex(3)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--network",
            "none",
            "--name",
            name,
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        ],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    try:
        for _ in range(60):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                    capture_output=True,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            raise TimeoutError("Isolated PostgreSQL restore container did not become ready")
        with tempfile.TemporaryFile() as dump:
            shutil.copyfileobj(archive.extractfile("database.dump"), dump)
            dump.seek(0)
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                ],
                stdin=dump,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        counts = json.loads(
            subprocess.check_output(["docker", "exec", name, "psql", "-U", "postgres", "-Atc", COUNTS])
        )
        if counts != metadata["counts"]:
            raise ValueError("Restored counts or migration revision differ from the backup")
    finally:
        subprocess.run(["docker", "stop", name], stdout=subprocess.DEVNULL, check=True)


def verify(manifest, snapshot):
    with tempfile.TemporaryFile(dir=DESTINATION) as out:
        subprocess.run([*RESTIC, "dump", snapshot, "/papyrus-release.tar"], stdout=out, check=True)
        out.seek(0)
        if (
            os.fstat(out.fileno()).st_size != manifest["bytes"]
            or hashlib.file_digest(out, "sha256").hexdigest() != manifest["sha256"]
        ):
            raise ValueError("Restored archive differs from the host snapshot")
        out.seek(0)
        with tarfile.open(fileobj=out, mode="r:") as archive:
            metadata = json.load(archive.extractfile("metadata.json"))
            if metadata["release"] != manifest["release"] or not archive.getmember("media").isdir():
                raise ValueError("Restored archive does not match the requested release")
            if not archive.getmember("deploy/production.env").size or not any(
                member.isfile() and member.name.startswith("deploy/secrets/") for member in archive
            ):
                raise ValueError("Restored archive is missing deployment configuration or keys")
            restore_database(archive, metadata, manifest["id"])


def process():
    manifest = json.loads(subprocess.check_output([*HOST, "pending"]))
    if not manifest:
        return
    job = manifest["id"]
    if not re.fullmatch(r"[0-9a-f]{32}", job):
        raise ValueError("Invalid backup id")
    tag = "release-backup-" + job
    snapshots = json.loads(subprocess.check_output([*RESTIC, "snapshots", "--json", "--tag", tag]))
    if not snapshots:
        print("Saving encrypted backup for", manifest["release"]["version"], flush=True)
        subprocess.run(
            [
                *RESTIC,
                "backup",
                "--quiet",
                "--tag",
                tag,
                "--stdin-filename",
                "papyrus-release.tar",
                "--stdin-from-command",
                "--",
                *HOST,
                "stream",
                job,
            ],
            check=True,
        )
        snapshots = json.loads(subprocess.check_output([*RESTIC, "snapshots", "--json", "--tag", tag]))
    if len(snapshots) != 1:
        raise ValueError("Expected one encrypted snapshot for this deployment")
    snapshot = snapshots[0]["id"]
    print("Verifying encrypted snapshot", snapshot[:12], flush=True)
    verify(manifest, snapshot)
    receipt = {"id": job, "sha256": manifest["sha256"], "snapshot": snapshot}
    receipt_path = DESTINATION / ("verified-" + job + ".json")
    receipt_path.write_text(json.dumps(manifest | receipt, indent=2) + "\n")
    subprocess.run([*HOST, "confirm", job], input=json.dumps(receipt).encode(), check=True)
    print("Confirmed off-host restore for", manifest["release"]["version"], flush=True)


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:] == ["init"]:
        init_repository()
    elif sys.argv[1:] in ([], ["install"]):
        DESTINATION.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (DESTINATION / ".release-worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sys.exit(
                    "A backup is in progress; retry installation after it finishes" if sys.argv[1:] else 0
                )
            if sys.argv[1:]:
                install_agent()
            else:
                process()
    else:
        sys.exit("Usage: release_backup_mac.py [install|init]")
