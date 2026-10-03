"""Check the committed workspace reader pin against the committed client lock."""

import re
import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def check(root: Path) -> None:
    client = git(root, "ls-tree", "HEAD", "client").split()[2]
    reader = git(root, "ls-tree", "HEAD", "reader").split()[2]
    manifest = git(root / "client", "show", f"{client}:app/pubspec.yaml")
    lock = git(root / "client", "show", f"{client}:app/pubspec.lock")
    for source, field in [(manifest, "ref"), (lock, "resolved-ref")]:
        block = re.search(r"(?m)^  papyrus_reader:\n((?:    .*\n?)+)", source)
        pin = (
            re.search(rf'{field}:\s*["\x27]?([a-f0-9]{{40}})\b', block[1])
            if block
            else None
        )
        if pin is None or pin[1] != reader:
            raise ValueError(
                f"Committed client {field} must match workspace reader {reader}"
            )
    print(f"Committed reader references agree: {reader}")


if __name__ == "__main__":
    try:
        check(Path(__file__).resolve().parent.parent)
    except (IndexError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Reader reference check failed: {error}") from error
