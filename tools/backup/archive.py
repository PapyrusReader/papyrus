"""Capture a consistent Papyrus release backup and rehearse its PostgreSQL restore."""

import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

API = "papyrus-production-api-1"
DB = "papyrus-production-database-1"
COUNTS = """SELECT json_build_object('users',(SELECT count(*) FROM users),
'books',(SELECT count(*) FROM books),'media_assets',(SELECT count(*) FROM media_assets),
'migration',(SELECT version_num FROM alembic_version))"""
DEPLOY = Path("/srv/apps/papyrus/deploy")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


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
    media = next(
        m["Source"]
        for m in info["Mounts"]
        if m["Destination"] == "/var/lib/papyrus/media"
    )
    tree = job / "contents"
    (tree / "media").mkdir(parents=True)
    (tree / "deploy").mkdir()
    sync = ["rsync", "-a", "--delete", media + "/", str(tree / "media") + "/"]
    media_bytes = (
        int(run("du", "-sk", media, capture_output=True).stdout.split()[0]) * 1024
    )
    db_bytes = int(
        database(
            "psql",
            "-Atc",
            "SELECT pg_database_size(current_database())",
            capture_output=True,
        ).stdout
    )
    if shutil.disk_usage(job).free < 2 * media_bytes + 3 * db_bytes + 1024**3:
        raise RuntimeError(
            "Insufficient free space for backup capture and isolated restore"
        )
    first = subprocess.run(sync, check=False).returncode
    if first not in (0, 24):
        raise RuntimeError(f"Media pre-copy failed ({first})")

    try:
        run("docker", "stop", "--time", "30", API, stdout=subprocess.DEVNULL)
        run(sync[0], "--checksum", *sync[1:])
        with (tree / "database.dump").open("wb") as out:
            database("pg_dump", "-Fc", stdout=out)
        counts = json.loads(
            database("psql", "-Atc", COUNTS, capture_output=True).stdout
        )
        for name in ("production.env", "compose.yml", "Caddyfile"):
            shutil.copy2(DEPLOY / name, tree / "deploy" / name)
        shutil.copytree(DEPLOY / "secrets", tree / "deploy/secrets")
    finally:
        run("docker", "start", API, stdout=subprocess.DEVNULL)

    metadata = {
        "release": request,
        "source_image": info["Config"]["Image"],
        "counts": counts,
        "database_image": json.loads(
            run("docker", "inspect", DB, capture_output=True).stdout
        )[0]["Config"]["Image"],
    }
    (tree / "metadata.json").write_text(json.dumps(metadata))
    archive = job / "backup.tar"
    run(
        "tar",
        "-cf",
        archive,
        "-C",
        tree,
        "metadata.json",
        "database.dump",
        "deploy",
        "media",
    )
    shutil.rmtree(tree)
    return archive


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
            "--pull=never",
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
                    [
                        "docker",
                        "exec",
                        name,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "postgres",
                    ],
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            raise TimeoutError(
                "Isolated PostgreSQL restore container did not become ready"
            )
        with tempfile.TemporaryFile(dir=Path(archive.name).parent) as dump:
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
            subprocess.check_output(
                ["docker", "exec", name, "psql", "-U", "postgres", "-Atc", COUNTS]
            )
        )
        if counts != metadata["counts"]:
            raise ValueError(
                "Restored counts or migration revision differ from the backup"
            )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", "-v", name], stdout=subprocess.DEVNULL, check=True
        )
