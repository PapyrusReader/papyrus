"""Resumable release coordination. GitHub remains the source of observed state."""

import argparse
import contextlib
import fcntl
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from release import version_tuple
from release_progress import activity, command_label, update

PR_FIELDS = (
    "number,url,state,body,headRefName,baseRefName,headRefOid,baseRefOid,mergeCommit,"
    "isDraft,mergeStateStatus,reviewDecision,statusCheckRollup"
)
COMPONENTS = ("server", "client", "website", "workspace")


class ReleaseError(RuntimeError):
    pass


def command(args, cwd, *, optional=False):
    update(command_label(args, cwd))
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120, check=False)

    if result.returncode and not optional:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ReleaseError("\n".join(detail[-3:]) or f"{args[0]} exited {result.returncode}")

    return result


def git(path, *args, optional=False):
    result = command(["git", "-C", str(path), *map(str, args)], path, optional=optional)
    return None if result.returncode else result.stdout.strip()


def read_file(path, revision, name):
    return git(path, "show", f"{revision}:{name}") + "\n"


def package_version(name, read):
    if name == "client":
        match = re.search(r"^version: (\d+\.\d+\.\d+)\+(\d+)\s*$", read("app/pubspec.yaml"), re.M)

        if not match:
            raise ReleaseError("Invalid client version")

        return match[1], int(match[2])

    if name == "server":
        version = tomllib.loads(read("pyproject.toml"))["project"]["version"]
        package = next(p for p in tomllib.loads(read("uv.lock"))["package"] if p["name"] == "papyrus-server")

        if package["version"] != version:
            raise ReleaseError("Server version differs from uv.lock")

        return version, None

    if name == "website":
        return json.loads(read("package.json"))["version"], None

    manifest = json.loads(read("release.json"))
    return manifest["version"], manifest["android_build_number"]


def version_files(name, read, version, build):
    old, _ = package_version(name, read)

    if name == "client":
        return {
            "app/pubspec.yaml": re.sub(
                r"^version: .*?$",
                f"version: {version}+{build}",
                read("app/pubspec.yaml"),
                count=1,
                flags=re.M,
            )
        }

    if name == "server":
        project = read("pyproject.toml").replace(f'version = "{old}"', f'version = "{version}"', 1)
        lock, count = re.subn(
            r'(name = "papyrus-server"\nversion = ")[^"]+(")',
            lambda match: match[1] + version + match[2],
            read("uv.lock"),
            count=1,
        )

        if count != 1:
            raise ReleaseError("Cannot locate server package in uv.lock")

        return {"pyproject.toml": project, "uv.lock": lock}

    files = {}

    for filename in ("package.json", "package-lock.json"):
        document = json.loads(read(filename))
        document["version"] = version

        if filename == "package-lock.json":
            document["packages"][""]["version"] = version

        files[filename] = json.dumps(document, indent=2) + "\n"

    return files


def step(name, stage, detail="", action=None, **fields):
    return {"component": name, "stage": stage, "detail": detail, "action": action, **fields}


class GitHub:
    def __init__(self, root):
        self.root = root

    def call(self, *args):
        return command(["gh", *map(str, args)], self.root).stdout.strip()

    def data(self, *args):
        return json.loads(self.call(*args))

    def releases(self, repo):
        pages = self.data("api", f"repos/{repo}/releases?per_page=100", "--paginate", "--slurp")
        return [publication for page in pages for publication in page]

    def api(self, endpoint, *, missing=False):
        result = command(["gh", "api", endpoint], self.root, optional=missing)

        if result.returncode:
            if "HTTP 404" in result.stderr:
                return None

            raise ReleaseError(result.stderr.strip())

        return json.loads(result.stdout)

    def find_pr(self, repo, head, base, marker, *, allow_unrelated=False):
        candidates = self.data(
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--head",
            head,
            "--base",
            base,
            "--limit",
            "100",
            "--json",
            PR_FIELDS,
        )
        matches = [pr for pr in candidates if marker in pr["body"]]

        if len(matches) > 1:
            raise ReleaseError(f"{repo}: multiple PRs for {marker}")

        if matches:
            return matches[0]

        if not allow_unrelated and any(pr["state"] == "OPEN" for pr in candidates):
            raise ReleaseError(f"{repo}: an unrelated {head} → {base} PR is open")

        return None

    def pr(self, repo, number):
        return self.data("pr", "view", number, "--repo", repo, "--json", PR_FIELDS)

    def merge_blocker(self, repo, pr):
        if pr["isDraft"]:
            return "draft PR (mark ready)"

        if pr["reviewDecision"] == "CHANGES_REQUESTED":
            return "changes requested (address review feedback)"

        checks = pr["statusCheckRollup"] or []

        if not checks:
            return "CI checks not reported yet"

        failed = []
        pending = []

        for check in checks:
            result = check.get("conclusion") or check.get("state")
            name = check.get("name") or check.get("context") or "unnamed check"

            if result in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE", "STARTUP_FAILURE"):
                failed.append(name)
            elif result not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                pending.append(name)

        for names, label in ((failed, "CI failed"), (pending, "CI running")):
            if names:
                extra = f" (+{len(names) - 2})" if len(names) > 2 else ""
                return f"{label}: {', '.join(names[:2])}{extra}"

        owner, name = repo.split("/")
        query = """query($owner:String!,$name:String!,$number:Int!,$endCursor:String) {
          repository(owner:$owner,name:$name) { pullRequest(number:$number) {
            reviewThreads(first:100,after:$endCursor) {
              nodes { isResolved } pageInfo { hasNextPage endCursor }
            }
          } }
        }"""
        pages = self.data(
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={pr['number']}",
        )

        if any(
            not thread["isResolved"]
            for page in pages
            for thread in page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        ):
            return "unresolved review threads"

        if pr["mergeStateStatus"] != "CLEAN":
            reasons = {
                "DIRTY": "merge conflicts (resolve on the PR branch)",
                "BEHIND": "PR branch is behind its base (update branch)",
                "UNKNOWN": "GitHub is checking mergeability (retry status)",
                "BLOCKED": "GitHub blocks merge (check PR requirements)",
            }
            return reasons.get(pr["mergeStateStatus"], f"GitHub merge state: {pr['mergeStateStatus'].lower()}")

        # REVIEW_REQUIRED describes review policy even when GitHub exempts this
        # actor. CLEAN is the effective merge state. GitHub still enforces the
        # normal merge request, without --admin or local author-based exceptions.
        return None


class Manager:
    def __init__(self, root, github=None):
        self.root = root
        self.storage = root / ".local/releases"
        self.github = github or GitHub(root)
        self.fetched = set()

    @contextlib.contextmanager
    def lock(self):
        self.storage.mkdir(parents=True, exist_ok=True)

        with (self.storage / "manager.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ReleaseError("Another release command is running") from error

            yield

    def save(self, plan):
        self.storage.mkdir(parents=True, exist_ok=True)
        destination = self.storage / f"{plan['id']}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(plan, indent=2) + "\n")
        temporary.replace(destination)

    def load(self, selector):
        if not re.fullmatch(r"(?:papyrus-|website-)?\d+\.\d+\.\d+(?:\+\d+)?", selector):
            raise ReleaseError("Use a version or release ID")

        records = {}

        for directory in (self.root / "releases", self.storage):
            for path in directory.glob("*.json"):
                plan = json.loads(path.read_text())

                if selector in (plan["id"], plan["version"], plan["id"].removeprefix("papyrus-")):
                    records[plan["id"]] = plan

        if len(records) != 1:
            raise ReleaseError("Release not found or ambiguous. Use release list or release plan.")

        return next(iter(records.values()))

    def path(self, name):
        return self.root if name == "workspace" else self.root / name

    def fetch(self, name):
        if name in self.fetched:
            return

        git(
            self.path(name),
            "fetch",
            "--quiet",
            "--no-recurse-submodules",
            "origin",
            "+refs/heads/development:refs/remotes/origin/development",
            "+refs/heads/master:refs/remotes/origin/master",
            "--tags",
        )
        self.fetched.add(name)

    def repo(self, name):
        url = git(self.path(name), "remote", "get-url", "origin")
        match = re.fullmatch(r"(?:https://github.com/|git@github.com:)([\w.-]+/[\w.-]+?)(?:\.git)?", url)

        if not match:
            raise ReleaseError(f"{name}: origin must be a github.com repository")

        return match[1]

    def revision(self, name, ref):
        return git(self.path(name), "rev-parse", "--verify", f"{ref}^{{commit}}", optional=True)

    def ancestor(self, name, ancestor, descendant):
        return git(self.path(name), "merge-base", "--is-ancestor", ancestor, descendant, optional=True) is not None

    def version(self, name, revision):
        return package_version(name, lambda filename: read_file(self.path(name), revision, filename))

    def matches(self, plan, name, revision):
        expected_build = plan["build"] if name in ("client", "workspace") else None
        return self.version(name, revision) == (plan["version"], expected_build)

    def create_plan(self, version, build, kind):
        version_tuple(version)

        if kind == "papyrus" and (build is None or not 0 < build <= 2100000000):
            raise ReleaseError("A valid --android-build is required")

        if kind == "website" and build is not None:
            raise ReleaseError("Website releases do not use --android-build")

        release_id = f"{kind}-{version}" + (f"+{build}" if build is not None else "")
        existing = self.storage / f"{release_id}.json"

        if existing.exists():
            return json.loads(existing.read_text())

        plan = {"schema": 1, "id": release_id, "version": version, "build": build, "components": {}}
        names = ("server", "client", "workspace") if kind == "papyrus" else ("website",)

        for name in names:
            self.fetch(name)
            source = self.revision(name, "origin/development")
            current, number = self.version(name, source)
            released_version, released_build = self.version(name, "origin/master")

            if version_tuple(version) < max(version_tuple(current), version_tuple(released_version)):
                raise ReleaseError(f"{name}: target version precedes development or master")

            if name == "client" and build < number:
                raise ReleaseError("Android build number cannot decrease")

            if name == "client" and version != current and build <= number:
                raise ReleaseError("Android build number must increase")

            if name == "client" and (version, build) != (released_version, released_build) and build <= released_build:
                raise ReleaseError("Android build number must exceed master")

            tag = None if name == "workspace" else f"v{version}" + (f"+{build}" if name == "client" else "")
            plan["components"][name] = {
                "repo": self.repo(name),
                "source": source,
                "accepted_development": source,
                "tag": tag,
                "prs": {},
            }

        self.save(plan)
        return plan

    def marker(self, plan, stage):
        return f"<!-- papyrus-release:{plan['id']}:{stage} -->"

    def branch(self, plan):
        return f"release/prepare-{plan['id']}"

    def get_pr(self, plan, name, stage, head, base, *, allow_unrelated=False):
        component = plan["components"][name]
        number = component["prs"].get(stage)

        if number:
            pr = self.github.pr(component["repo"], number)
        elif allow_unrelated:
            pr = self.github.find_pr(component["repo"], head, base, self.marker(plan, stage), allow_unrelated=True)
        else:
            pr = self.github.find_pr(component["repo"], head, base, self.marker(plan, stage))

        if pr and (
            pr["headRefName"] != head or pr["baseRefName"] != base or self.marker(plan, stage) not in pr["body"]
        ):
            raise ReleaseError(f"{name}: PR identity changed")

        return pr

    def pr_step(self, plan, name, stage, pr, expected):
        link = pr["url"]

        if pr["state"] != "OPEN":
            return step(name, "blocked", f"{stage} PR closed: {link}")

        if pr["headRefOid"] != expected:
            return step(name, "blocked", f"{stage} head changed: {link}")

        blocker = self.github.merge_blocker(plan["components"][name]["repo"], pr)
        return step(
            name,
            stage,
            f"{blocker or 'ready to merge'} {link}",
            None if blocker else "merge",
            pr=pr["number"],
            head=expected,
            base=pr["baseRefOid"],
            blocker=blocker,
        )

    def publication(self, plan, name, revision):
        component = plan["components"][name]
        tag_revision = self.revision(name, component["tag"])

        if tag_revision and tag_revision != revision:
            return step(name, "blocked", "tag points to a different commit")

        runs = self.github.data(
            "run",
            "list",
            "--repo",
            component["repo"],
            "--workflow",
            "release.yml",
            "--branch",
            "master",
            "--commit",
            revision,
            "--limit",
            "100",
            "--json",
            "databaseId,status,conclusion,url,headSha",
        )
        run = runs[0] if runs else None
        published = self.github.api(f"repos/{component['repo']}/releases/tags/{component['tag']}", missing=True)

        if not published or published["draft"] or not tag_revision:
            detail = "waiting for publication"

            if run:
                detail = f"{run['conclusion'] or run['status']} {run['url']}"

            return step(name, "publishing", detail, revision=revision, run=run)

        verification = component.get("verification", {})

        if verification.get("revision") == revision:
            return step(name, "verified", "operator verified", revision=revision, run=run)

        if name != "server" and run and run["conclusion"] == "success":
            jobs = self.github.data("run", "view", run["databaseId"], "--repo", component["repo"], "--json", "jobs")
            expected = {"Verify public website"}

            if name == "client":
                expected = {"Deploy and verify web app", "Confirm internal release identity"}

            passed = {
                result["name"]
                for job in jobs["jobs"]
                if job["conclusion"] == "success"
                for result in job["steps"]
                if result["conclusion"] == "success"
            }

            if expected <= passed:
                return step(name, "verified", "delivery workflow passed", revision=revision, run=run)

        detail = "deployment verification required"

        if name != "server" and run and run["conclusion"] != "success":
            detail = f"delivery {run['conclusion'] or run['status']} {run['url']}"

        return step(name, "published", detail, revision=revision, run=run)

    def component_step(self, plan, name):
        component = plan["components"][name]
        tag_revision = self.revision(name, component["tag"]) if component["tag"] else None

        if tag_revision:
            if not self.matches(plan, name, tag_revision):
                return step(name, "blocked", "tag version mismatch")

            if not self.ancestor(name, tag_revision, "origin/master"):
                return step(name, "blocked", "tag is not on master")

            promotion = self.get_pr(plan, name, "promote", "development", "master", allow_unrelated=True)

            if promotion and (promotion["state"] != "MERGED" or promotion["mergeCommit"]["oid"] != tag_revision):
                return step(name, "blocked", "tag differs from promotion PR")

            return self.publication(plan, name, tag_revision)

        preparation = self.get_pr(plan, name, "prepare", self.branch(plan), "development")
        development = self.revision(name, "origin/development")

        if preparation and preparation["state"] == "MERGED":
            prepared = preparation["mergeCommit"]["oid"]

            if preparation["headRefOid"] != component.get("prepared_head"):
                return step(name, "blocked", "preparation head changed")

            if not self.matches(plan, name, prepared):
                return step(name, "blocked", "preparation merged with the wrong version")
        elif preparation:
            if development != component["accepted_development"]:
                return step(name, "blocked", "development changed, use accept-head")

            return self.pr_step(plan, name, "prepare", preparation, component.get("prepared_head", ""))
        elif self.matches(plan, name, component["source"]):
            prepared = component["source"]
        else:
            if development != component["accepted_development"]:
                return step(name, "blocked", "development changed, use accept-head")

            return step(name, "planned", "create preparation PR", "prepare")

        promotion = self.get_pr(plan, name, "promote", "development", "master")

        if promotion and promotion["state"] == "MERGED":
            revision = promotion["mergeCommit"]["oid"]

            if not self.matches(plan, name, revision) or not self.ancestor(name, prepared, revision):
                return step(name, "blocked", "promotion does not include the prepared version")

            return self.publication(plan, name, revision)

        accepted = component.get("promotion_head", prepared)

        if development != accepted:
            return step(name, "blocked", "development changed, use accept-head")

        if not self.ancestor(name, prepared, development) or not self.matches(plan, name, development):
            return step(name, "blocked", "development does not contain the prepared version")

        if promotion:
            return self.pr_step(plan, name, "promote", promotion, accepted)

        return step(name, "prepared", "create promotion PR", "promote", head=accepted)

    def snapshot_matches(self, plan, revision, revisions):
        if not self.matches(plan, "workspace", revision):
            return False

        for name, sha in revisions.items():
            entry = git(self.root, "ls-tree", revision, name).split()

            if len(entry) < 3 or entry[:2] != ["160000", "commit"] or entry[2] != sha:
                return False

        return True

    def workspace_step(self, plan, observations):
        if not all(observations[name]["stage"] == "verified" for name in ("server", "client")):
            return step("workspace", "waiting", "after server and client releases are verified")

        revisions = {name: observations[name]["revision"] for name in ("server", "client")}
        component = plan["components"]["workspace"]
        master = self.revision("workspace", "origin/master")

        if self.snapshot_matches(plan, master, revisions):
            return step("workspace", "verified", "snapshot recorded", revision=master)

        preparation = self.get_pr(plan, "workspace", "prepare", self.branch(plan), "development")
        promotion = self.get_pr(plan, "workspace", "promote", "development", "master")

        if promotion and promotion["state"] == "MERGED":
            revision = promotion["mergeCommit"]["oid"]

            if self.snapshot_matches(plan, revision, revisions):
                return step("workspace", "verified", "snapshot recorded", revision=revision)

            return step("workspace", "blocked", "released snapshot differs")

        development = self.revision("workspace", "origin/development")

        if preparation and preparation["state"] == "MERGED":
            prepared = preparation["mergeCommit"]["oid"]

            if not self.snapshot_matches(plan, prepared, revisions):
                return step("workspace", "blocked", "prepared snapshot differs")

            accepted = component.get("promotion_head", prepared)

            if development != accepted or not self.ancestor("workspace", prepared, development):
                return step("workspace", "blocked", "development changed, use accept-head")

            if not self.snapshot_matches(plan, development, revisions):
                return step("workspace", "blocked", "development snapshot differs")

            if promotion:
                return self.pr_step(plan, "workspace", "promote", promotion, accepted)

            return step("workspace", "prepared", "create promotion PR", "promote", head=accepted)

        if development != component["accepted_development"]:
            return step("workspace", "blocked", "development changed, use accept-head")

        if preparation:
            return self.pr_step(plan, "workspace", "prepare", preparation, component.get("prepared_head", ""))

        return step("workspace", "planned", "create snapshot PR", "prepare", revisions=revisions)

    def observe(self, plan):
        observations = {}

        for name in plan["components"]:
            self.fetch(name)

            if self.repo(name) != plan["components"][name]["repo"]:
                raise ReleaseError(f"{name}: origin differs from release plan")

        for name in plan["components"]:
            if name != "workspace":
                observations[name] = self.component_step(plan, name)

        if "client" in observations and observations["server"]["stage"] != "verified":
            client = observations["client"]

            if client["action"] == "promote" or client["stage"] == "promote":
                observations["client"] = step("client", "waiting", "after server deployment is verified")

        if "workspace" in plan["components"]:
            observations["workspace"] = self.workspace_step(plan, observations)

        return observations

    def sync_step(self, plan, name, released):
        master = self.revision(name, "origin/master")

        if self.ancestor(name, released, "origin/development"):
            return step(name, "complete")

        if master != released:
            return step(name, "blocked", "master advanced beyond this release, synchronize manually")

        pr = self.get_pr(plan, name, "sync", "master", "development")

        if pr:
            return self.pr_step(plan, name, "sync", pr, master)

        return step(name, "sync", "create synchronization PR", "sync", head=master)

    def status(self, plan):
        observations = self.observe(plan)

        if all(item["stage"] == "verified" for item in observations.values()):
            return {name: self.sync_step(plan, name, observations[name]["revision"]) for name in plan["components"]}

        return observations

    def create_pr(self, plan, name, stage, head, base):
        component = plan["components"][name]
        existing = self.get_pr(plan, name, stage, head, base)

        if existing:
            component["prs"][stage] = existing["number"]
            self.save(plan)
            return

        body = (
            f"{self.marker(plan, stage)}\n\n"
            f"{stage.capitalize()} {plan['id']} for {name}.\n\n"
            "Managed by `tools/papyrus release`. "
            "Required checks and applicable review rules must pass before merging.\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as handle:
            handle.write(body)
            handle.flush()
            self.github.call(
                "pr",
                "create",
                "--repo",
                component["repo"],
                "--head",
                head,
                "--base",
                base,
                "--title",
                f"{stage.capitalize()} {plan['id']}",
                "--body-file",
                handle.name,
            )

        pr = self.get_pr(plan, name, stage, head, base)

        if not pr:
            raise ReleaseError(f"{name}: PR creation not yet visible, rerun the command")

        component["prs"][stage] = pr["number"]
        self.save(plan)

    def prepare(self, plan, name, revisions=None):
        component = plan["components"][name]
        repository = self.path(name)
        branch = self.branch(plan)
        source = component["accepted_development"]

        if self.revision(name, "origin/development") != source:
            raise ReleaseError(f"{name}: development changed")

        with tempfile.TemporaryDirectory(prefix="papyrus-release-") as directory:
            worktree = Path(directory) / "tree"
            git(repository, "worktree", "add", "--quiet", "--detach", worktree, source)

            try:
                if name == "workspace":
                    files = {
                        "release.json": json.dumps(
                            {
                                "version": plan["version"],
                                "android_build_number": plan["build"],
                            },
                            indent=2,
                        )
                        + "\n"
                    }
                    archive = json.loads(json.dumps(plan))
                    workspace = archive["components"]["workspace"]
                    workspace["prs"] = {}
                    workspace.pop("prepared_head", None)
                    workspace.pop("promotion_head", None)
                    files[f"releases/{plan['id']}.json"] = json.dumps(archive, indent=2) + "\n"

                    for child, revision in revisions.items():
                        git(worktree, "update-index", "--add", "--cacheinfo", "160000", revision, child)
                else:
                    files = version_files(
                        name, lambda filename: (worktree / filename).read_text(), plan["version"], plan["build"]
                    )

                for filename, content in files.items():
                    destination = worktree / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(content)

                git(worktree, "add", "--", *files)
                tree = git(worktree, "write-tree")
                remote = git(repository, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")

                if remote:
                    git(repository, "fetch", "--quiet", "--no-recurse-submodules", "origin", f"refs/heads/{branch}")
                    head = self.revision(name, "FETCH_HEAD")

                    if git(repository, "rev-parse", f"{head}^{{tree}}") != tree or not self.ancestor(
                        name, source, head
                    ):
                        raise ReleaseError(f"{name}: existing preparation branch differs from plan")
                else:
                    git(worktree, "commit", "--quiet", "-m", f"Prepare {plan['id']}")
                    head = git(worktree, "rev-parse", "HEAD")
                    git(worktree, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")

                component["prepared_head"] = head
                self.save(plan)
            finally:
                git(repository, "worktree", "remove", "--force", worktree)

        self.create_pr(plan, name, "prepare", branch, "development")

    def execute(self, plan, item, merge):
        name = item["component"]
        action = item["action"]
        component = plan["components"][name]

        if action == "prepare":
            self.prepare(plan, name, item.get("revisions"))
        elif action == "promote":
            component["promotion_head"] = item["head"]
            self.save(plan)
            self.create_pr(plan, name, "promote", "development", "master")
        elif action == "sync":
            self.create_pr(plan, name, "sync", "master", "development")
        elif action == "merge" and merge:
            pr = self.github.pr(component["repo"], item["pr"])
            blocker = self.github.merge_blocker(component["repo"], pr)

            if blocker or pr["state"] != "OPEN" or pr["headRefOid"] != item["head"] or pr["baseRefOid"] != item["base"]:
                raise ReleaseError(f"{name}: PR changed or blocked, refresh status")

            self.github.call(
                "pr", "merge", item["pr"], "--repo", component["repo"], "--merge", "--match-head-commit", item["head"]
            )
        else:
            return False

        return True

    def advance(self, plan, *, merge=False, dry_run=False, prepare_only=False):
        observations = self.status(plan)
        performed = []

        for name, item in observations.items():
            if prepare_only and (name == "workspace" or item["action"] != "prepare"):
                continue

            if dry_run:
                continue

            if not item["action"] or (item["action"] == "merge" and not merge):
                continue

            self.fetched.clear()
            current = self.status(plan)[name]

            if any(current.get(field) != item.get(field) for field in ("stage", "action", "head", "base", "revisions")):
                raise ReleaseError(f"{name}: release changed, refresh status")

            if self.execute(plan, current, merge):
                stage = item["stage"] if item["action"] == "merge" else item["action"]
                number = plan["components"][name]["prs"].get(stage)
                link = f" https://github.com/{plan['components'][name]['repo']}/pull/{number}" if number else ""
                performed.append(f"{name}: {item['action']}{link}")

        return observations, performed

    def accept_head(self, plan, name):
        if name not in plan["components"]:
            raise ReleaseError("Component is not in this release")

        self.fetch(name)
        component = plan["components"][name]
        head = self.revision(name, "origin/development")
        current_version, current_build = self.version(name, head)

        if version_tuple(current_version) > version_tuple(plan["version"]):
            raise ReleaseError("Development version exceeds this release")

        if name in ("client", "workspace") and current_build > plan["build"]:
            raise ReleaseError("Development build number exceeds this release")

        if name == "client" and current_version != plan["version"] and current_build >= plan["build"]:
            raise ReleaseError("Android build number must increase")

        preparation = self.get_pr(plan, name, "prepare", self.branch(plan), "development")

        if preparation and preparation["state"] == "MERGED":
            if not self.matches(plan, name, head) or not self.ancestor(name, preparation["mergeCommit"]["oid"], head):
                raise ReleaseError("Development must contain the prepared version")

            component["promotion_head"] = head
        elif preparation and preparation["state"] == "OPEN":
            if not self.matches(plan, name, preparation["headRefOid"]):
                raise ReleaseError("Preparation PR must contain the target version")

            component["accepted_development"] = head
            component["prepared_head"] = preparation["headRefOid"]
        elif preparation:
            raise ReleaseError("Reopen the preparation PR before accepting its head")
        else:
            component["accepted_development"] = head
            component["source"] = head

            if self.matches(plan, name, head):
                component["promotion_head"] = head

        self.save(plan)
        return head

    def verify(self, plan, name, revision, evidence):
        if name not in plan["components"] or name == "workspace":
            raise ReleaseError("Choose a released component")

        observations = self.observe(plan)
        item = observations[name]

        if item["stage"] not in ("published", "verified") or item.get("revision") != revision:
            raise ReleaseError("Verification must identify the published release commit exactly")

        if not evidence.strip():
            raise ReleaseError("Deployment evidence is required")

        plan["components"][name]["verification"] = {
            "revision": revision,
            "evidence": evidence,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save(plan)

    def retry(self, plan, name, stage):
        observations = self.observe(plan)

        if name not in observations or name == "workspace":
            raise ReleaseError("Choose a released component")

        item = observations[name]
        component = plan["components"][name]

        if stage in ("web", "play"):
            if name != "client" or item["stage"] not in ("published", "verified"):
                raise ReleaseError("Delivery retry requires a published client release")

            self.github.call(
                "workflow",
                "run",
                "delivery.yml",
                "--repo",
                component["repo"],
                "--ref",
                "master",
                "-f",
                f"tag={component['tag']}",
                "-f",
                f"destination={stage}",
            )
            return

        run = item.get("run")

        if not run and item["stage"] == "publishing" and self.revision(name, "origin/master") == item["revision"]:
            self.github.call("workflow", "run", "release.yml", "--repo", component["repo"], "--ref", "master")
            return

        if not run or run["status"] != "completed" or run["conclusion"] not in ("failure", "cancelled", "timed_out"):
            raise ReleaseError("No failed release workflow to retry")

        self.github.call("run", "rerun", run["databaseId"], "--repo", component["repo"], "--failed")


def next_step(plan, observations):
    if plan.get("source") == "github":
        return None

    command = "tools/papyrus release"
    release_id = plan["id"]
    actions = {item["action"] for item in observations.values()} - {None}

    if actions:
        suffix = " --merge" if "merge" in actions else ""
        operation = "advance"

        if actions == {"prepare"} and observations.get("workspace", {}).get("action") != "prepare":
            operation = "prepare"

        return f"{command} {operation} {release_id}{suffix}"

    instructions = (
        ("CI failed", "Fix the failing CI checks in the linked PRs"),
        ("changes requested", "Address the requested review changes"),
        ("unresolved review threads", "Resolve the review threads in the linked PRs"),
        ("draft PR", "Mark the linked PR ready for review"),
        ("merge conflicts", "Resolve conflicts on the PR branch"),
        ("GitHub blocks merge", "Open the linked PR to inspect its unmet GitHub requirements"),
        ("CI running", "Wait for CI"),
        ("CI checks not reported", "Wait for CI to start"),
        ("GitHub is checking", "Wait for GitHub to finish checking mergeability"),
    )

    for prefix, instruction in instructions:
        if any((item.get("blocker") or "").startswith(prefix) for item in observations.values()):
            return f"{instruction}, then rerun: {command} status {release_id}"

    for name, item in observations.items():
        if "development changed" in item["detail"] or "PR branch is behind" in item["detail"]:
            return (
                f"Inspect/update the {name} PR branch, then run: {command} accept-head {release_id} --component {name}"
            )

        if (item.get("run") or {}).get("conclusion") in ("failure", "cancelled", "timed_out"):
            return (
                f"Inspect the failed workflow, then run: {command} retry {release_id} "
                f"--component {name} --stage release"
            )

        if item["stage"] == "published":
            return (
                f"Deploy and verify {name}, then record evidence: {command} verify {release_id} "
                f"--component {name} --revision {item['revision']} --evidence '<verification details>'"
            )

        if item["stage"] == "publishing":
            return f"Wait for publication, then rerun: {command} status {release_id}"

    return None


def render(plan, observations, as_json=False, *, dry_run=False, merge=False):
    next_action = next_step(plan, observations)

    if as_json:
        print(
            json.dumps(
                {
                    "release": plan["id"],
                    "components": list(observations.values()),
                    "next": next_action,
                    "dry_run": dry_run,
                },
                indent=2,
            )
        )
        return

    print(plan["id"])

    for name, item in observations.items():
        detail = item["detail"]

        if dry_run and item["action"] == "merge" and merge:
            stage = {"prepare": "preparation", "promote": "promotion", "sync": "synchronization"}.get(
                item["stage"],
                item["stage"],
            )
            detail = detail.replace("ready to merge", f"would merge {stage} PR", 1)
        elif dry_run and item["action"] in ("prepare", "promote", "sync"):
            detail = f"would {detail}"

        print(f"  {name:<10} {item['stage']:<11} {detail}".rstrip())

    if dry_run and not merge and any(item["action"] == "merge" for item in observations.values()):
        print("Dry run: ready PRs need --merge.")

    if next_action:
        print(f"Next: {next_action}")


def positive_integer(value):
    number = int(value)

    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")

    return number


def parser():
    result = argparse.ArgumentParser(
        prog="papyrus release",
        description="Prepare, inspect, and advance releases. Output is compact by default.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Record release intent without remote writes")
    plan.add_argument("version")
    plan.add_argument("--android-build", type=int)
    plan.add_argument("--component", choices=("papyrus", "website"), default="papyrus")
    plan.add_argument("--json", action="store_true")
    listing = commands.add_parser("list", help="List release statuses from saved plans and GitHub")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--component", choices=("all", "papyrus", "website"), default="all")
    listing.add_argument("--limit", type=positive_integer, default=20, help="Maximum rows (default: 20)")

    for name in ("status", "prepare", "advance", "accept-head", "verify", "retry"):
        subparser = commands.add_parser(name)
        subparser.add_argument("release")
        subparser.add_argument("--json", action="store_true")

        if name in ("advance", "prepare"):
            subparser.add_argument("--dry-run", action="store_true")

        if name == "advance":
            subparser.add_argument("--merge", action="store_true", help="Merge eligible PRs in this step")

        if name in ("accept-head", "verify", "retry"):
            subparser.add_argument("--component", choices=COMPONENTS, required=True)

        if name == "verify":
            subparser.add_argument("--revision", required=True)
            subparser.add_argument("--evidence", required=True, help="Operator's deployment verification evidence")

        if name == "retry":
            subparser.add_argument("--stage", choices=("release", "web", "play"), default="release")

    return result


def main(argv=None, *, root=None):
    options = parser().parse_args(argv)
    manager = Manager(root or Path(__file__).resolve().parents[1])

    try:
        with manager.lock():
            if options.command == "list":
                from release_catalog import Catalog, render_list

                catalog = Catalog(manager)

                with activity("Loading releases", enabled=not options.json):
                    rows = catalog.list(options.component, options.limit)

                render_list(rows, catalog.errors, options.json)
                return int(bool(catalog.errors))

            if options.command == "plan":
                with activity("Planning release", enabled=not options.json):
                    plan = manager.create_plan(options.version, options.android_build, options.component)
                    observations = manager.status(plan)

                render(plan, observations, options.json)
                return 0

            if options.command == "status":
                from release_catalog import Catalog

                with activity("Checking release status", enabled=not options.json):
                    plan, observations = Catalog(manager).inspect(options.release)

                render(plan, observations, options.json)
                return 0

            plan = manager.load(options.release)

            if options.command in ("prepare", "advance"):
                with activity("Checking next release step", enabled=not options.json):
                    observations, performed = manager.advance(
                        plan,
                        merge=getattr(options, "merge", False),
                        dry_run=options.dry_run,
                        prepare_only=options.command == "prepare",
                    )

                if performed:
                    next_action = f"tools/papyrus release status {plan['id']}"
                    print(
                        json.dumps({"release": plan["id"], "actions": performed, "next": next_action})
                        if options.json
                        else "\n".join([*performed, f"Next: {next_action}"])
                    )
                else:
                    render(
                        plan,
                        observations,
                        options.json,
                        dry_run=options.dry_run,
                        merge=getattr(options, "merge", False),
                    )
            elif options.command == "accept-head":
                with activity("Checking release commits", enabled=not options.json):
                    revision = manager.accept_head(plan, options.component)

                print(
                    json.dumps({"revision": revision})
                    if options.json
                    else f"{options.component}: accepted {revision[:12]}"
                )
            elif options.command == "verify":
                with activity("Checking published release", enabled=not options.json):
                    manager.verify(plan, options.component, options.revision, options.evidence)

                print(
                    json.dumps({"verified": options.component})
                    if options.json
                    else f"{options.component}: verification recorded"
                )
            elif options.command == "retry":
                with activity("Checking release retry", enabled=not options.json):
                    manager.retry(plan, options.component, options.stage)

                print(
                    json.dumps({"retry": options.component, "stage": options.stage})
                    if options.json
                    else f"{options.component}: retry requested"
                )

        return 0
    except (ReleaseError, OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from release_manager import main as entrypoint

    sys.exit(entrypoint())
