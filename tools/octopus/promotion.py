"""Prepare protected release PRs before handing the merged manifest to Octopus."""
import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from pipeline import ORG, REPO, SHA, fetch, parse_release
from release import version_tuple
from release_manager import GitHub, git, package_version, read_file, version_files
from source import resolve

COMPONENTS = ("client", "server")


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


def ref(repo, branch):
    entry = git(repo, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    return fetch(repo, branch) if entry else None


def ancestor(repo, older, newer):
    return git(repo, "merge-base", "--is-ancestor", older, newer, optional=True) is not None


def reader_pin(client, revision):
    lock = read_file(client, revision, "app/pubspec.lock")
    block = re.search(r"(?m)^  papyrus_reader:\n((?:    .*\n)+)", lock)
    match = re.search(r'(?m)^      resolved-ref: "?([0-9a-f]{40})"?$', block[1]) if block else None
    if not match:
        raise ValueError("Cannot resolve the client's locked reader revision")
    return match[1]


def sync_commit(repo, base, source):
    # A strict, up-to-date PR can include the latest development without advancing master.
    tree = git(repo, "merge-tree", "--write-tree", base, source)
    date = git(repo, "show", "-s", "--format=%cI", base)
    return subprocess.check_output(
        ["git", "commit-tree", tree, "-p", base, "-p", source, "-m", "Sync promoted source into development"],
        cwd=repo, text=True, env=dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date),
    ).strip()


class Promotion:
    def __init__(self, workspace, clones, number, base):
        self.workspace = workspace
        self.clones = clones
        self.number = number
        self.base = base
        self.branch = f"release/{number}"
        self.version, self.build = parse_release(number)
        self.hub = GitHub(workspace)

    def request_review(self, name, number, head):
        workflow = "review-release.yml"
        run = self.hub.data("api", f"repos/{REPO}/actions/workflows/{workflow}/dispatches",
                            "-f", "ref=master", "-F", "return_run_details=true",
                            "-f", f"inputs[repo]={name.split('/')[-1]}", "-f", f"inputs[pr]={number}",
                            "-f", f"inputs[number]={self.number}", "-f", f"inputs[head]={head}",
                            "-f", f"inputs[base]={self.base}")
        run_id = int(run["workflow_run_id"])
        print(f"Release review: https://github.com/{REPO}/actions/runs/{run_id}", flush=True)
        while True:
            result = self.hub.api(f"repos/{REPO}/actions/runs/{run_id}", missing=True)
            if result and result["status"] == "completed":
                if result["conclusion"] != "success":
                    raise ValueError(f"Release review run {run_id} ended with {result['conclusion']}")
                return
            time.sleep(10)

    def merge_pr(self, repo, branch, target, head, title, *, sync=False):
        name = REPO if repo == self.workspace else f"{ORG}/{repo.name}"
        marker = f"Papyrus {self.number} · {branch} into {target}"
        pr = self.hub.find_pr(name, branch, target, marker)
        if not pr:
            publish_ref(repo, branch, head)
            with tempfile.TemporaryDirectory() as tmp:
                body = Path(tmp) / "body.md"
                body.write_text(marker + "\n")
                self.hub.call("pr", "create", "--repo", name, "--base", target, "--head", branch,
                              "--title", title, "--body-file", str(body))
            pr = self.hub.find_pr(name, branch, target, marker)
        if not pr:
            raise ValueError(f"Cannot locate release PR in {name}")
        print(pr["url"], flush=True)
        approved_head = None
        while True:
            pr = self.hub.pr(name, pr["number"])
            if pr["headRefOid"] != head or pr["state"] == "CLOSED":
                raise ValueError(f"Release PR changed or closed: {pr['url']}")
            if pr["state"] == "MERGED":
                merged = pr["mergeCommit"]["oid"]
                current = fetch(repo, target)
                if not ancestor(repo, merged, current) or not ancestor(repo, head, merged):
                    raise ValueError(f"Unexpected merge result for {pr['url']}")
                if not sync:
                    git(repo, "diff", "--exit-code", "--quiet", head, merged)
                return merged
            gate, reason = self.hub.merge_gate(name, pr)
            if sync and gate == "behind":
                return None
            if gate in ("ci_pending", "mergeability_pending"):
                time.sleep(15)
                continue
            if gate not in (None, "review_pending"):
                raise ValueError(f"{pr['url']}: {reason}")
            if not ancestor(repo, fetch(repo, target), head):
                if sync:
                    return None
                raise ValueError(f"{target} changed since source selection: {pr['url']}")
            if approved_head != head:
                self.request_review(name, pr["number"], head)
                approved_head = head
                continue
            if gate == "review_pending":
                raise ValueError(f"GitHub still requires a qualifying approval on {pr['url']}")
            self.hub.call("pr", "merge", pr["number"], "--repo", name, "--merge", "--match-head-commit", head)
            time.sleep(10)

    def sync(self, name, source, phase):
        repo = self.clones[name]
        branch = f"release-sync/{self.number}/{phase}"
        while True:
            base = fetch(repo, "development")
            if ancestor(repo, source, base):
                return
            head = sync_commit(repo, base, source)
            old = ref(repo, branch)
            if old and old != head:
                parents = git(repo, "show", "-s", "--format=%P", old).split()
                if (len(parents) != 2 or parents[1] != source or not ancestor(repo, parents[0], base)
                        or sync_commit(repo, parents[0], source) != old):
                    raise ValueError(f"Unexpected changes to {name}/{branch}")
                git(repo, "push", f"--force-with-lease=refs/heads/{branch}:{old}", "origin",
                    f"{head}:refs/heads/{branch}")
            if self.merge_pr(repo, branch, "development", head, f"Sync {name} for {self.number}", sync=True):
                return

    def inputs(self, *, existing=False):
        repo = self.workspace
        branch = f"release-inputs/{self.number}"
        previous = json.loads(read_file(repo, self.base, "release.json"))
        if version_tuple(self.version) <= version_tuple(previous["version"]) or self.build <= previous["android_build_number"]:
            raise ValueError("The version and Android build must both increase")
        saved = ref(repo, branch)
        if not saved:
            if existing:
                raise ValueError("Missing frozen release inputs")
            current = fetch(repo, "master")
            if current != self.base:
                raise ValueError("Workspace master advanced before release preparation")
            for name in COMPONENTS:
                self.sync(name, fetch(self.clones[name], "master"), "before")
            pins = {name: fetch(self.clones[name], "development") for name in COMPONENTS}
            saved = make_commit(repo, self.base, f"Freeze inputs for {self.number}", {}, pins)
            publish_ref(repo, branch, saved)
        pins = {name: git(repo, "rev-parse", f"{saved}:{name}") for name in COMPONENTS}
        if saved != make_commit(repo, self.base, f"Freeze inputs for {self.number}", {}, pins):
            raise ValueError("Saved release inputs belong to a different workspace revision")
        for name, sha in pins.items():
            if not ancestor(self.clones[name], sha, fetch(self.clones[name], "development")):
                raise ValueError(f"Saved {name} source is outside development")
        return pins

    def version_commit(self, name, parent):
        repo = self.clones[name]

        def read(path):
            return read_file(repo, parent, path)

        previous, build = package_version(name, read)
        if version_tuple(self.version) <= version_tuple(previous) or (build is not None and self.build <= build):
            raise ValueError(f"The release must advance the version of {name} at its selected commit")
        files = version_files(name, read, self.version, self.build)
        return make_commit(repo, parent, f"Version Papyrus {self.number}", files, {})

    def manifest_commit(self, pins):
        pins = {**pins, "reader": reader_pin(self.clones["client"], pins["client"])}
        release = dict(version=self.version, android_build_number=self.build, orchestrator="octopus")
        return make_commit(self.workspace, self.base, f"Release Papyrus {self.number}",
                           {"release.json": json.dumps(release, indent=2) + "\n"}, pins)

    def promote(self):
        pins = {}
        for name, parent in self.inputs().items():
            head = self.version_commit(name, parent)
            pins[name] = self.merge_pr(self.clones[name], self.branch, "master", head, f"Release {name} {self.number}")
        head = self.manifest_commit(pins)
        manifest = self.merge_pr(self.workspace, self.branch, "master", head, f"Release Papyrus {self.number}")
        self.verify(manifest)
        return manifest

    def verify(self, manifest):
        pins = {}
        current = fetch(self.workspace, "master")
        for name in COMPONENTS:
            sha, tag = resolve(self.workspace, manifest, name, current)
            if tag != f"v{self.number}" or not ancestor(self.clones[name], sha, fetch(self.clones[name], "master")):
                raise ValueError("Manifest does not identify the promoted release")
            pins[name] = sha
        return pins

    def review_pr(self, name, number, head):
        repo = self.workspace if name == "papyrus" else self.clones[name]
        endpoint = f"repos/{ORG}/{name}/pulls/{number}"
        pr = self.hub.api(endpoint)
        author = os.environ["RELEASE_AUTHOR"]
        if (not SHA.fullmatch(head) or pr["state"] != "open" or pr["draft"]
                or pr["user"]["login"] != author or pr["head"]["repo"]["full_name"] != f"{ORG}/{name}"
                or pr["head"]["sha"] != head or not ancestor(self.workspace, self.base, fetch(self.workspace, "master"))):
            raise ValueError("Unexpected release PR identity")
        branch = pr["head"]["ref"]
        target = pr["base"]["ref"]
        base = fetch(repo, target)
        if target == "development" and name in COMPONENTS:
            master = fetch(repo, "master")
            if branch == f"release-sync/{self.number}/before":
                source = master
            elif branch == f"release-sync/{self.number}/after":
                manifest = fetch(self.workspace, "master")
                source = self.verify(manifest)[name]
            else:
                raise ValueError("Unexpected sync branch")
            expected = sync_commit(repo, base, source)
        elif target == "master" and branch == self.branch:
            sources = self.inputs(existing=True)
            if name in COMPONENTS:
                expected = self.version_commit(name, sources[name])
            else:
                pins = {}
                for component, parent in sources.items():
                    owner = f"{ORG}/{component}"
                    merged = self.hub.find_pr(owner, branch, "master", f"Papyrus {self.number} · {branch} into master")
                    candidate = self.version_commit(component, parent)
                    if not merged or merged["state"] != "MERGED" or merged["headRefOid"] != candidate:
                        raise ValueError(f"Missing matching merged {component} release PR")
                    revision = merged["mergeCommit"]["oid"]
                    component_repo = self.clones[component]
                    if not ancestor(component_repo, revision, fetch(component_repo, "master")):
                        raise ValueError(f"{component} merge is outside master")
                    git(component_repo, "diff", "--quiet", candidate, revision)
                    pins[component] = revision
                expected = self.manifest_commit(pins)
        else:
            raise ValueError("Unexpected release PR target or branch")
        if expected != head or not ancestor(repo, base, head):
            raise ValueError("The proposed commit differs from the independently reconstructed release")
        reviewer = self.hub.call("api", "user", "--jq", ".login")
        if reviewer.casefold() == author.casefold():
            raise ValueError("The reviewer must be independent of the PR author")
        reviews = self.hub.data("api", endpoint + "/reviews", "--paginate", "--slurp")
        previous = [r for page in reviews for r in page if r["user"]["login"] == reviewer
                    and r["commit_id"] == head and r["state"] != "COMMENTED"]
        if not previous or previous[-1]["state"] != "APPROVED":
            self.hub.call("api", endpoint + "/reviews", "-f", "event=APPROVE", "-f", f"commit_id={head}",
                          "-f", "body=Verified the release commit against protected source history.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sync-manifest")
    mode.add_argument("--review", nargs=3, metavar=("REPO", "PR", "HEAD"))
    parser.add_argument("--base", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    workspace = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        clones = {}
        for name in COMPONENTS:
            clones[name] = Path(tmp) / name
            git(workspace, "clone", "--bare", "--filter=blob:none", f"https://github.com/{ORG}/{name}.git", clones[name])
        if not args.base or not SHA.fullmatch(args.base):
            raise ValueError("Expected a full workspace base SHA")
        release = Promotion(workspace, clones, args.number, args.base)
        if args.review:
            name, pr, head = args.review
            if name not in (*COMPONENTS, "papyrus") or not pr.isdecimal():
                raise ValueError("Expected a Papyrus release repository and PR number")
            release.review_pr(name, int(pr), head)
        elif args.sync_manifest:
            for name, sha in release.verify(args.sync_manifest).items():
                release.sync(name, sha, "after")
        else:
            manifest = release.promote()
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                print(f"manifest={manifest}", file=output)


if __name__ == "__main__":
    main()
