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
from source import RELEASE_PATHS, resolve
from release import version_tuple
from release_deployment import DeploymentError, ServerVersionMismatch, migration_heads, published_image, verify_server
from release_manager import GitHub, git, read_file, version_files

ROOT = Path("/var/lib/papyrus-release/workspace")
STATE = Path("/var/lib/papyrus-release/octopus")
ORG = "PapyrusReader"
REPO = f"{ORG}/papyrus"
SHA = re.compile(r"[0-9a-f]{40}")
STAGES = ("prepare", "server-build", "client-build", "server-deploy", "publish", "web", "play")


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


def make_commit(repo, parent, title, files, pins):
    with tempfile.TemporaryDirectory() as tmp:
        date = git(repo, "show", "-s", "--format=%cI", parent)
        env = dict(os.environ, GIT_INDEX_FILE=f"{tmp}/index", GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        def indexed(*args, data=None):
            return subprocess.check_output(["git", *args], cwd=repo, env=env, text=True, input=data).strip()

        indexed("read-tree", parent)
        for path, content in files.items():
            blob = indexed("hash-object", "-w", "--stdin", data=content)
            indexed("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
        for name, sha in pins.items():
            indexed("update-index", "--add", "--cacheinfo", f"160000,{sha},{name}")
        return indexed("commit-tree", indexed("write-tree"), "-p", parent, "-m", title)


def publish_ref(repo, ref, revision):
    ref = f"refs/heads/{ref}"
    existing = git(repo, "ls-remote", "--heads", "origin", ref)
    if existing:
        if existing.split()[0] != revision:
            raise ValueError(f"Release branch {ref} already contains a different commit")
        return
    git(repo, "push", f"--force-with-lease={ref}:", "origin", f"{revision}:{ref}")


def snapshot(number):
    version, build = parse_release(number)
    base = fetch(ROOT, "master")
    previous = json.loads(read_file(ROOT, base, "release.json"))
    if version_tuple(version) <= version_tuple(previous["version"]) or build <= previous["android_build_number"]:
        raise ValueError("The release version and Android build must increase from workspace master")
    integration = {name: fetch(ROOT / name, "development") for name in ("client", "server")}
    pins = {}
    for name, parent in integration.items():
        repo = ROOT / name
        files = version_files(name, lambda path: read_file(repo, parent, path), version, build)
        pins[name] = make_commit(repo, parent, f"Version Papyrus {number}", files, {})
    lock = read_file(ROOT / "client", pins["client"], "app/pubspec.lock")
    reader_block = re.search(r"(?m)^  papyrus_reader:\n((?:    .*\n)+)", lock)
    reader = re.search(r'(?m)^      resolved-ref: "?(\w{40})"?$', reader_block[1]) if reader_block else None
    if not reader or not SHA.fullmatch(reader[1]):
        raise ValueError("Cannot resolve the client's locked reader revision")
    pins["reader"] = reader[1]
    manifest = {"version": version, "android_build_number": build, "orchestrator": "octopus"}
    head = make_commit(ROOT, base, f"Release Papyrus {number}", {"release.json": json.dumps(manifest, indent=2) + "\n"}, pins)
    return {"base": base, "head": head, "pins": pins, "sources": integration}


def prepare(hub, directory, number):
    for repo, path, expected in (
        ("papyrus", "tools/octopus/source.py", "def resolve("),
        ("client", ".github/workflows/release.yml", "Build client ${{ inputs.manifest }}"),
        ("client", ".github/workflows/publish.yml", "sealed-release"),
        ("server", ".github/workflows/release.yml", "Build server ${{ inputs.manifest }}"),
    ):
        contents = hub.call("api", "-H", "Accept: application/vnd.github.raw+json",
                            f"repos/{ORG}/{repo}/contents/{path}?ref=master")
        if expected not in contents:
            raise ValueError(f"Install the Octopus release workflow on {repo}/master before starting a release")
    path = directory / "plan.json"
    if not path.exists():
        save(path, snapshot(number))
    plan = json.loads(path.read_text())
    branch = f"octopus/{number}"
    for name in ("client", "server"):
        publish_ref(ROOT / name, branch, plan["pins"][name])
    publish_ref(ROOT, branch, plan["head"])
    marker = f"Papyrus release manifest for {number}"
    pr = hub.find_pr(REPO, branch, "master", marker)
    if not pr:
        body = directory / "pull-request.md"
        body.write_text(marker + "\n\n" + "\n".join(
            f"- {name}: https://github.com/{ORG}/{name}/commit/{sha}" for name, sha in plan["pins"].items()
        ) + "\n\nMerging authorizes Octopus to deploy this exact snapshot to production.\n")
        print(hub.call("pr", "create", "--repo", REPO, "--base", "master", "--head", branch,
                       "--title", f"Release Papyrus {number}", "--body-file", str(body)), flush=True)
        pr = hub.find_pr(REPO, branch, "master", marker)
    print(f"Waiting for the workspace release approval: {pr['url']}", flush=True)
    while True:
        pr = hub.pr(REPO, pr["number"])
        if pr["headRefOid"] != plan["head"] or pr["state"] == "CLOSED":
            raise ValueError("The manifest PR changed or was closed")
        if pr["state"] == "MERGED":
            break
        gate, reason = hub.merge_gate(REPO, pr)
        if gate is None:
            hub.call("pr", "merge", pr["number"], "--repo", REPO, "--merge", "--match-head-commit", plan["head"])
        elif gate not in ("ci_pending", "review_pending", "mergeability_pending", "review_threads"):
            raise ValueError(reason)
        time.sleep(25)
    revision = pr["mergeCommit"]["oid"]
    git(ROOT, "diff", "--quiet", revision, plan["head"], "--", *RELEASE_PATHS)
    resolve(ROOT, revision, "client", fetch(ROOT, "master"))
    save(directory / "manifest.json", revision)


def approved(directory, number):
    revision = json.loads((directory / "manifest.json").read_text())
    _, tag = resolve(ROOT, revision, "client", fetch(ROOT, "master"))
    if tag != f"v{number}":
        raise ValueError("Workspace manifest does not identify this Octopus release")
    return revision


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
        run = hub.data("api", run_url)
        if run["display_title"] != title or run["head_branch"] != "master" or run["path"] != f".github/workflows/{workflow}":
            raise ValueError("GitHub run identity differs from the dispatched workflow")
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


def main(stage, number):
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
            prepare(hub, directory, number)
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
    args = parser.parse_args()
    main(args.stage, args.release)
