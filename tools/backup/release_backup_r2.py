#!/usr/bin/env python3
"""Upload an encrypted release snapshot to R2, then verify a fresh download before deployment."""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

from archive import restore_database, snapshot

CONFIG = Path("/etc/papyrus-release/r2")
SPOOL = Path("/var/lib/papyrus-release-r2")
OBJECT = re.compile(
    r"backups/([0-9]+\.[0-9]+\.[0-9]+)/([0-9a-f]{32})-([0-9a-f]{64})\.tar\.age"
)


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def connect(settings):
    import boto3
    from botocore.config import Config

    account = settings["account_id"]
    if not re.fullmatch("[0-9a-f]{32}", account):
        raise ValueError("Expected a Cloudflare account ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.eu.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=settings["access_key_id"],
        aws_secret_access_key=settings["secret_access_key"],
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def validate_request(request):
    if not (
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", request["version"])
        and re.fullmatch(r"[0-9a-f]{40}", request["revision"])
        and re.fullmatch(
            r"ghcr\.io/papyrusreader/server@sha256:[0-9a-f]{64}", request["image"]
        )
    ):
        raise ValueError("Invalid release request")


def verify(client, bucket, key, job, expected=None):
    match = OBJECT.fullmatch(key)
    if not match:
        raise ValueError("Expected a Papyrus backup object key")
    version, job_id, digest = match.groups()
    encrypted = job / "download.age"
    client.download_file(bucket, key, str(encrypted))
    restored = job / "restored.tar"
    subprocess.run(
        ["age", "-d", "-i", CONFIG / "identity", "-o", restored, encrypted], check=True
    )
    encrypted.unlink()
    if checksum(restored) != digest:
        raise ValueError(
            "Downloaded archive checksum differs from the captured snapshot"
        )
    with tarfile.open(restored, "r:") as archive:
        metadata = json.load(archive.extractfile("metadata.json"))
        request = metadata["release"]
        validate_request(request)
        if request["version"] != version or (
            expected is not None and request != expected
        ):
            raise ValueError("Downloaded archive has the wrong release request")
        if not archive.getmember("media").isdir():
            raise ValueError("Missing media directory")
        for filename in (
            "production.env",
            "Caddyfile",
            "compose.yml",
            "secrets/powersync-private.pem",
            "secrets/powersync-public.pem",
        ):
            member = archive.getmember("deploy/" + filename)
            if not member.isfile() or not member.size:
                raise ValueError(f"Missing deployment file {filename}")
        restore_database(archive, metadata, job_id)
    return {
        "bucket": bucket,
        "key": key,
        "sha256": digest,
        "bytes": restored.stat().st_size,
        "release": request,
    }


def backup(client, bucket, job, request):
    validate_request(request)
    if not shutil.which("age") or not all(
        (CONFIG / name).is_file() for name in ("recipients", "identity")
    ):
        raise RuntimeError(
            "Install age and configure the backup recipient and restore identity"
        )
    client.head_bucket(Bucket=bucket)
    source = snapshot(job, request)
    key = f"backups/{request['version']}/{uuid.uuid4().hex}-{checksum(source)}.tar.age"
    encrypted = job / "upload.age"
    subprocess.run(
        ["age", "-R", CONFIG / "recipients", "-o", encrypted, source], check=True
    )
    source.unlink()
    client.upload_file(str(encrypted), bucket, key)
    encrypted.unlink()
    receipt = verify(client, bucket, key, job, request)
    with tempfile.NamedTemporaryFile("w", dir=job, delete=False) as output:
        json.dump(receipt, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(output.name, SPOOL / "last-verified.json")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        metavar="OBJECT_KEY",
        help="Repeat the restore drill for an existing backup",
    )
    args = parser.parse_args()
    os.umask(0o077)
    settings = json.loads((CONFIG / "storage.json").read_text())
    client = connect(settings)
    SPOOL.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SPOOL) as tmp:
        job = Path(tmp)
        if args.verify:
            receipt = verify(client, settings["bucket"], args.verify, job)
        else:
            receipt = backup(client, settings["bucket"], job, json.load(sys.stdin))
    print(
        f"Verified PostgreSQL restore from {receipt['bucket']}/{receipt['key']}",
        flush=True,
    )


if __name__ == "__main__":

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    main()
