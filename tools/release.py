#!/usr/bin/env python3
"""Check and update the release snapshot across independent Papyrus repositories."""

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError("Version must be MAJOR.MINOR.PATCH")
    return tuple(map(int, value.split(".")))


def read(root: Path, path: str, committed: bool = False) -> str:
    if not committed:
        return (root / path).read_text()
    component, relative = path.split("/", 1) if "/" in path else ("", path)
    if component in ("client", "server"):
        tree = subprocess.check_output(["git", "-C", str(root), "ls-tree", "HEAD", component], text=True)
        revision = tree.split()[2]
        return subprocess.check_output(
            ["git", "-C", str(root / component), "show", f"{revision}:{relative}"], text=True
        )
    return subprocess.check_output(["git", "-C", str(root), "show", f"HEAD:{path}"], text=True)


def state(root: Path, committed: bool = False) -> tuple[dict[str, Any], str, str, str]:
    manifest = json.loads(read(root, "release.json", committed))
    version_tuple(manifest["version"])
    client = read(root, "client/app/pubspec.yaml", committed)
    server = read(root, "server/pyproject.toml", committed)
    lock = read(root, "server/uv.lock", committed)
    match = re.search(r"^version: ([0-9]+\.[0-9]+\.[0-9]+)\+([0-9]+)\s*$", client, re.M)
    if not match:
        raise ValueError("Invalid client version")
    expected = manifest["version"]
    if match[1] != expected or int(match[2]) != manifest["android_build_number"]:
        raise ValueError("Client version/build number differs from release.json")
    if not 0 < manifest["android_build_number"] <= 2100000000:
        raise ValueError("Android build number is out of range")
    if tomllib.loads(server)["project"]["version"] != expected:
        raise ValueError("Server package version differs from release.json")
    locked = next(p for p in tomllib.loads(lock)["package"] if p["name"] == "papyrus-server")
    if locked["version"] != expected:
        raise ValueError("Server lockfile version differs from release.json")
    return manifest, client, server, lock


def bump(root: Path, version: str, number: int) -> None:
    manifest, client, server, lock = state(root)
    if version_tuple(version) < version_tuple(manifest["version"]):
        raise ValueError("Version cannot decrease")
    if not manifest["android_build_number"] < number <= 2100000000:
        raise ValueError("Android build number must increase and remain within the Play limit")
    old = manifest["version"]
    replacements = {
        "client/app/pubspec.yaml": re.sub(
            r"^version: .*?$", f"version: {version}+{number}", client, count=1, flags=re.M
        ),
        "server/pyproject.toml": server.replace(f'version = "{old}"', f'version = "{version}"', 1),
        "server/uv.lock": lock.replace(
            f'name = "papyrus-server"\nversion = "{old}"', f'name = "papyrus-server"\nversion = "{version}"', 1
        ),
        "release.json": json.dumps({"version": version, "android_build_number": number}, indent=2) + "\n",
    }
    if lock != replacements["server/uv.lock"] or version == old:
        for path, contents in replacements.items():
            (root / path).write_text(contents)
    else:
        raise ValueError("Cannot locate the server package entry in uv.lock")
    state(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--committed", action="store_true")
    update = subparsers.add_parser("bump")
    update.add_argument("version")
    update.add_argument("--android-build", required=True, type=int)
    args = parser.parse_args()
    if args.command == "bump":
        bump(ROOT, args.version, args.android_build)
        print(
            "Updated client, server and workspace release intent. Commit component PRs first, then record their commits in the workspace."
        )
    else:
        manifest, *_ = state(ROOT, args.committed)
        print(f"Coordinated release {manifest['version']}, Android build {manifest['android_build_number']}")


if __name__ == "__main__":
    main()
