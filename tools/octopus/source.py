"""Resolve a component at a workspace release still authorized by protected master."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

RELEASE_PATHS = ("release.json", "client", "server", "reader")


def resolve(repo, manifest, component, current):
    if not re.fullmatch(r"[0-9a-f]{40}", manifest):
        raise ValueError("Expected a full workspace manifest SHA")

    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()

    git("merge-base", "--is-ancestor", manifest, current)
    git("diff", "--quiet", manifest, current, "--", *RELEASE_PATHS)
    config = json.loads(git("show", f"{manifest}:release.json"))
    if config.get("orchestrator") != "octopus":
        raise ValueError("This workspace manifest is not an Octopus release")
    tag = f"v{config['version']}+{config['android_build_number']}"
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+\+[1-9][0-9]*", tag):
        raise ValueError("Invalid Papyrus release identity")
    entry = git("ls-tree", manifest, component)
    match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t" + re.escape(component), entry)
    if not match:
        raise ValueError("Expected a pinned component in the workspace tree")
    return match[1], tag


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["server", "client"])
    parser.add_argument("manifest")
    args = parser.parse_args()
    revision, tag = resolve(Path.cwd(), args.manifest, args.component, "HEAD")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        print(f"revision={revision}\ntag={tag}", file=output)
