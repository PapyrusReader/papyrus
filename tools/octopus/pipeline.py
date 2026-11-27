#!/usr/bin/env python3
"""Release steps invoked by Octopus on the Papyrus release host."""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source import resolve
from release import version_tuple
from release_deployment import DeploymentError, ServerVersionMismatch, migration_heads, published_image, verify_server
from release_manager import GitHub, git, read_file

ROOT = Path("/var/lib/papyrus-release/workspace")
STATE = Path("/var/lib/papyrus-release/octopus")
ORG = "PapyrusReader"
REPO = f"{ORG}/papyrus"
SHA = re.compile(r"[0-9a-f]{40}")
STAGES = ("prepare", "server-build", "client-build", "server-deploy", "publish", "web", "play")
RUN_IDENTITY_ATTEMPTS = 7
RUN_IDENTITY_POLL_SECONDS = 5


def parse_release(number):
    version, separator, build = number.partition("+")
    version_tuple(version)
    if not separator or not re.fullmatch(r"[1-9][0-9]{0,9}", build) or int(build) > 2100000000:
        raise ValueError("Use MAJOR.MINOR.PATCH+ANDROID_BUILD")
    return version, int(build)


def save(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)


def fetch(repo, branch):
    ref = f"refs/heads/{branch}"
    revision = git(repo, "ls-remote", "--exit-code", "origin", ref).split()[0]
    if not SHA.fullmatch(revision):
        raise ValueError(f"Invalid Git revision for {branch}")
    git(repo, "fetch", "--quiet", "--no-recurse-submodules", "--no-tags", "--no-write-fetch-head", "origin", revision)
    return revision


def prepare(directory, number, manifest=None):
    path = directory / "manifest.json"
    if path.exists():
        saved = json.loads(path.read_text())
        if manifest is not None and manifest != saved:
            raise ValueError("This release already has a different workspace manifest")
        manifest = saved
    if not manifest or not SHA.fullmatch(manifest):
        raise ValueError("An approved workspace manifest SHA is required")
    _, tag = resolve(ROOT, manifest, "client", fetch(ROOT, "master"))
    if tag != f"v{number}":
        raise ValueError("Workspace manifest does not identify this Octopus release")
    save(path, manifest)


def approved(directory, number):
    revision = json.loads((directory / "manifest.json").read_text())
    _, tag = resolve(ROOT, revision, "client", fetch(ROOT, "master"))
    if tag != f"v{number}":
        raise ValueError("Workspace manifest does not identify this Octopus release")
    return revision


def verified_workflow_run(hub, run_url, workflow, title):
    expected = {"display_title": title, "head_branch": "master", "path": f".github/workflows/{workflow}"}
    # The dispatch can return a run ID before its full metadata is visible.
    for attempt in range(RUN_IDENTITY_ATTEMPTS):
        if attempt:
            time.sleep(RUN_IDENTITY_POLL_SECONDS)
        run = hub.api(run_url, missing=True)
        if run is not None and all(run.get(key) == value for key, value in expected.items()):
            return run

    actual = {key: run.get(key) for key in expected} if run is not None else None
    raise ValueError(f"Could not verify GitHub run {run_url}: expected {expected!r}, got {actual!r}")


def run_workflow(hub, directory, stage, repo, workflow, title, inputs):
    endpoint = f"repos/{ORG}/{repo}/actions"
    path = directory / f"{stage}.json"
    if not path.exists():
        save(path, {"dispatching": True})
        run = hub.data("api", f"{endpoint}/workflows/{workflow}/dispatches", "-f", "ref=master",
                       "-F", "return_run_details=true", *[v for k, val in inputs.items() for v in ("-f", f"inputs[{k}]={val}")])
        save(path, {"id": run["workflow_run_id"]})
    state = json.loads(path.read_text())
    if "id" not in state:
        # A lost HTTP response must not trigger another build or delivery.
        runs = hub.data("api", f"{endpoint}/workflows/{workflow}/runs?branch=master&event=workflow_dispatch&per_page=100")
        matches = [r["id"] for r in runs["workflow_runs"] if r["display_title"] == title]
        if len(matches) != 1:
            raise ValueError(f"Cannot uniquely reconcile dispatch of {title}; inspect GitHub Actions")
        state = {"id": matches[0]}
        save(path, state)
    run_url = f"{endpoint}/runs/{state['id']}"
    print(f"https://github.com/{ORG}/{repo}/actions/runs/{state['id']}", flush=True)
    first = True
    while True:
        run = verified_workflow_run(hub, run_url, workflow, title)
        if run["status"] == "completed":
            if run["conclusion"] == "success":
                jobs = hub.data("api", f"{run_url}/attempts/{run['run_attempt']}/jobs?per_page=100")
                # Failed-job reruns may omit successful jobs from earlier attempts.
                required = {"server-build": "image", "client-build": "seal", "publish": "publish", "web": "web", "play": "play"}[stage]
                success = any(j["name"] == required and j["conclusion"] == "success" for j in jobs["jobs"])
                if not success:
                    history = hub.data("api", f"{run_url}/jobs?filter=all&per_page=100", "--paginate", "--slurp")
                    results = [j for page in history for j in page["jobs"] if j["name"] == required]
                    success = bool(results) and max(results, key=lambda j: j["id"])["conclusion"] == "success"
                if not success:
                    raise ValueError(f"GitHub completed without a successful {required} job")
                return
            if first and state.get("failed_attempt") == run["run_attempt"]:
                hub.call("api", "--method", "POST", run_url + "/rerun-failed-jobs")
                state.pop("failed_attempt")
                save(path, state)
                time.sleep(12)
            else:
                state["failed_attempt"] = run["run_attempt"]
                save(path, state)
                raise ValueError(f"{stage} ended with {run['conclusion']}; retry this Octopus step after repair")
        first = False
        time.sleep(20)


def invoke_server_job(target, request):
    args = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "--", target["ssh_host"], "/usr/local/bin/papyrus-release-server",
    ]

    try:
        result = subprocess.run(args, input=json.dumps(request), text=True, capture_output=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeploymentError("Server deployment job was interrupted; check its state on the host") from error

    if result.returncode:
        raise DeploymentError(f"Server deployment job exited {result.returncode}; check its host log")


def server_deploy(hub, number, manifest):
    version, _ = parse_release(number)
    repo = ROOT / "server"
    revision = git(ROOT, "rev-parse", f"{manifest}:server")
    tag_revision = hub.call("api", f"repos/{ORG}/server/commits/v{version}", "--jq", ".sha")
    if tag_revision != revision:
        raise ValueError("Published server tag differs from the approved manifest")
    record = hub.call("release", "download", f"v{version}", "--repo", f"{ORG}/server", "--pattern", "image.txt", "--output", "-")
    target = json.loads((ROOT / ".local/release-deployment.json").read_text())
    paths = git(repo, "ls-tree", "-r", "--name-only", revision, "alembic/versions").splitlines()
    expected = dict(version=version, revision=revision, image_record=record,
                    migrations=migration_heads(read_file(repo, revision, path) for path in paths if path.endswith(".py")))
    try:
        verify_server(target, **expected)
    except ServerVersionMismatch as mismatch:
        if version_tuple(str(mismatch.actual)) >= version_tuple(version):
            raise ValueError("Refusing to replace a newer production version") from mismatch
        request = dict(version=version, revision=revision, image=published_image(record, version), manifest=manifest)
        invoke_server_job(target, request)
        verify_server(target, **expected)


def main(stage, number, manifest=None):
    parse_release(number)
    os.umask(0o077)
    directory = STATE / number
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    hub = GitHub(ROOT)
    dependencies = {
        "server-deploy": ("server-build", "client-build"),
        "publish": ("server-deploy",), "web": ("publish",), "play": ("publish",),
    }
    with (directory / f"{stage}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if stage == "prepare":
            prepare(directory, number, manifest)
            return
        manifest = approved(directory, number)
        for prerequisite in dependencies.get(stage, ()):
            receipt = directory / f"{prerequisite}.done"
            if not receipt.exists() or json.loads(receipt.read_text()) != manifest:
                raise ValueError(f"{prerequisite} must finish for this workspace manifest first")
        if stage == "server-deploy":
            server_deploy(hub, number, manifest)
        elif stage in ("client-build", "server-build"):
            repo = stage.split("-")[0]
            run_workflow(hub, directory, stage, repo, "release.yml", f"Build {repo} {manifest}", {"manifest": manifest})
        elif stage == "publish":
            build = json.loads((directory / "client-build.json").read_text())["id"]
            run_workflow(hub, directory, stage, "client", "publish.yml", f"Publish {manifest}", {"manifest": manifest, "build_run": str(build)})
        else:
            tag = f"v{number}"
            run_workflow(hub, directory, stage, "client", "delivery.yml", f"Deliver {tag} to {stage}", {"tag": tag, "destination": stage})
        save(directory / f"{stage}.done", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("release")
    parser.add_argument("--manifest")
    args = parser.parse_args()
    main(args.stage, args.release, args.manifest)
