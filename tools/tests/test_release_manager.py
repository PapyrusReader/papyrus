"""Exercise release recovery and ordering with real Git repositories and fake GitHub."""

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import release_manager as release  # noqa: E402


def git(path, *args):
    return release.git(path, *args)


class FakeGitHub:
    def __init__(self, fixture):
        self.fixture = fixture
        self.pulls = {}
        self.runs = {}
        self.releases = {}
        self.calls = []
        self.fail_create = False

    def ref(self, repo, ref):
        name = repo.split("/")[1]
        return git(self.fixture.remotes[name], "rev-parse", ref)

    def pr(self, repo, number):
        pr = copy.deepcopy(self.pulls[(repo, int(number))])

        if pr["state"] == "OPEN":
            pr["headRefOid"] = self.ref(repo, pr["headRefName"])
            pr["baseRefOid"] = self.ref(repo, pr["baseRefName"])

        return pr

    def find_pr(self, repo, head, base, marker, **kwargs):
        for (owner, number), pr in self.pulls.items():
            if owner == repo and pr["headRefName"] == head and pr["baseRefName"] == base and marker in pr["body"]:
                return self.pr(repo, number)

        return None

    def merge_blocker(self, repo, pr):
        return None if pr["mergeStateStatus"] == "CLEAN" else "checks pending or failing"

    def api(self, endpoint, **kwargs):
        return self.releases.get(endpoint)

    def data(self, *args):
        if args[:2] == ("run", "list"):
            repo = args[args.index("--repo") + 1]
            revision = args[args.index("--commit") + 1]
            return self.runs.get((repo, revision), [])

        if args[:2] == ("run", "view"):
            return {
                "jobs": [
                    {
                        "conclusion": "success",
                        "steps": [
                            {"name": name, "conclusion": "success"}
                            for name in (
                                "Verify public website",
                                "Deploy and verify web app",
                                "Confirm internal release identity",
                            )
                        ],
                    }
                ]
            }

        raise AssertionError(args)

    def call(self, *args):
        self.calls.append(args)
        repo = args[args.index("--repo") + 1]

        if args[:2] == ("pr", "create"):
            if self.fail_create:
                self.fail_create = False
                raise release.ReleaseError("connection interrupted")

            head = args[args.index("--head") + 1]
            base = args[args.index("--base") + 1]
            number = len(self.pulls) + 1
            self.pulls[(repo, number)] = {
                "number": number,
                "url": f"https://github.com/{repo}/pull/{number}",
                "state": "OPEN",
                "body": Path(args[args.index("--body-file") + 1]).read_text(),
                "headRefName": head,
                "baseRefName": base,
                "headRefOid": self.ref(repo, head),
                "baseRefOid": self.ref(repo, base),
                "mergeCommit": None,
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [],
            }
            return "created"

        if args[:2] == ("pr", "merge"):
            number = int(args[2])
            pr = self.pr(repo, number)
            assert args[args.index("--match-head-commit") + 1] == pr["headRefOid"]
            assert "--merge" in args and "--admin" not in args
            name = repo.split("/")[1]

            with tempfile.TemporaryDirectory() as directory:
                clone = Path(directory) / "clone"
                git(self.fixture.root, "clone", "--quiet", self.fixture.remotes[name], clone)
                self.fixture.identity(clone)
                git(clone, "checkout", "--quiet", "-B", pr["baseRefName"], f"origin/{pr['baseRefName']}")
                git(clone, "merge", "--quiet", "--no-ff", "-m", f"Merge PR {number}", f"origin/{pr['headRefName']}")
                revision = git(clone, "rev-parse", "HEAD")
                git(clone, "push", "--quiet", "origin", pr["baseRefName"])

            pr["state"] = "MERGED"
            pr["mergeCommit"] = {"oid": revision}
            self.pulls[(repo, number)] = pr
            return "merged"

        if args[:2] in (("workflow", "run"), ("run", "rerun")):
            return "requested"

        raise AssertionError(args)


class ReleaseManagerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "workspace"
        self.root.mkdir()
        self.remotes = {}
        fixtures = {
            "server": {
                "pyproject.toml": '[project]\nname = "papyrus-server"\nversion = "1.0.0"\n',
                "uv.lock": '[[package]]\nname = "papyrus-server"\nversion = "1.0.0"\n\n'
                '[[package]]\nname = "dependency"\nversion = "1.0.0"\n',
            },
            "client": {"app/pubspec.yaml": "name: papyrus\nversion: 1.0.0+1\n"},
            "website": {
                "package.json": json.dumps({"name": "site", "version": "1.0.0"}) + "\n",
                "package-lock.json": json.dumps(
                    {
                        "name": "site",
                        "version": "1.0.0",
                        "packages": {
                            "": {"name": "site", "version": "1.0.0"},
                            "node_modules/dependency": {"version": "1.0.0"},
                        },
                    }
                )
                + "\n",
            },
        }

        for name, files in fixtures.items():
            path = self.root / name
            path.mkdir()
            self.init_repo(name, path, files)

        self.init_repo(
            "workspace",
            self.root,
            {
                "release.json": '{"version": "1.0.0", "android_build_number": 1}\n',
                ".gitignore": ".local/\n",
            },
        )
        self.github = FakeGitHub(self)
        self.manager = release.Manager(self.root, self.github)
        self.manager.repo = lambda name: f"test/{name}"

    def identity(self, path):
        git(path, "config", "user.name", "Release test")
        git(path, "config", "user.email", "release@example.invalid")
        git(path, "config", "commit.gpgsign", "false")

    def init_repo(self, name, path, files):
        git(path, "init", "--quiet", "-b", "development")
        self.identity(path)

        for filename, value in files.items():
            target = path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)

        git(path, "add", ".")
        git(path, "commit", "--quiet", "-m", "Initial")
        git(path, "branch", "master")
        remote = Path(self.directory.name) / f"{name}.git"
        git(path, "clone", "--bare", "--quiet", path, remote)
        git(path, "remote", "add", "origin", remote)
        git(path, "fetch", "--quiet", "origin")
        self.remotes[name] = remote

    def plan(self, kind="papyrus"):
        return self.manager.create_plan("1.0.1", 2 if kind == "papyrus" else None, kind)

    def status(self, plan):
        self.manager.fetched.clear()
        return self.manager.status(plan)

    def advance(self, plan, **kwargs):
        self.manager.fetched.clear()
        return self.manager.advance(plan, **kwargs)

    def publish(self, plan, name, success=True):
        component = plan["components"][name]
        repo = component["repo"]
        revision = git(self.remotes[name], "rev-parse", "master")
        git(self.remotes[name], "tag", component["tag"], revision)
        self.github.releases[f"repos/{repo}/releases/tags/{component['tag']}"] = {"draft": False}
        self.github.runs[(repo, revision)] = [
            {
                "databaseId": 123,
                "headSha": revision,
                "status": "completed",
                "conclusion": "success" if success else "failure",
                "url": "https://github.com/run/123",
            }
        ]
        self.manager.fetched.clear()
        return revision

    def promote_server(self, plan):
        self.advance(plan, prepare_only=True)
        self.advance(plan, merge=True)
        self.advance(plan)
        self.advance(plan, merge=True)
        return self.publish(plan, "server")

    def healthy_server(self, revision):
        probe = patch.object(self.manager, "check_server_deployment", return_value={"revision": revision})
        self.addCleanup(probe.stop)
        return probe.start()

    def test_plan_and_dry_run_do_not_write_remotes_or_checkouts(self):
        before = {name: git(self.manager.path(name), "status", "--porcelain") for name in self.remotes}
        plan = self.plan()
        observations, actions = self.advance(plan, dry_run=True, merge=True)
        self.assertEqual(observations["server"]["action"], "prepare")
        self.assertEqual(actions, [])
        self.assertEqual(self.github.calls, [])
        self.assertEqual(before, {name: git(self.manager.path(name), "status", "--porcelain") for name in self.remotes})
        self.assertEqual(self.manager.load("1.0.1")["id"], "papyrus-1.0.1+2")

    def test_preparation_preserves_dirty_files_and_is_idempotent(self):
        path = self.root / "client/app/pubspec.yaml"
        path.write_text(path.read_text() + "# local override\n")
        plan = self.plan()
        self.advance(plan, prepare_only=True)
        before = list(self.github.calls)
        self.advance(plan, prepare_only=True)
        self.assertEqual(self.github.calls, before)
        self.assertIn("# local override", path.read_text())
        self.assertEqual(git(self.root / "client", "branch", "--show-current"), "development")
        self.assertEqual(len(self.github.pulls), 2)
        self.assertEqual(len(git(self.root / "client", "worktree", "list", "--porcelain").split("worktree ")) - 1, 1)
        lock = read_remote(self, "server", self.manager.branch(plan), "uv.lock")
        self.assertIn('name = "papyrus-server"\nversion = "1.0.1"', lock)
        self.assertIn('name = "dependency"\nversion = "1.0.0"', lock)

    def test_interruption_after_push_recovers_existing_branch(self):
        plan = self.plan()
        self.github.fail_create = True

        with self.assertRaisesRegex(release.ReleaseError, "interrupted"):
            self.advance(plan, prepare_only=True)

        branch = self.manager.branch(plan)
        revision = git(self.remotes["server"], "rev-parse", branch)
        plan = self.manager.load(plan["id"])
        self.advance(plan, prepare_only=True)
        self.assertEqual(revision, git(self.remotes["server"], "rev-parse", branch))
        self.assertEqual(len(self.github.pulls), 2)

    def test_manual_merge_and_entire_release_resume_through_snapshot_and_sync(self):
        plan = self.plan()
        server_revision = self.promote_server(plan)
        self.assertEqual(self.status(plan)["client"]["stage"], "waiting")
        self.healthy_server(server_revision)
        self.manager.verify(plan, "server", server_revision, "Deployed image digest and API health verified")
        self.advance(plan)
        self.advance(plan, merge=True)
        client_revision = self.publish(plan, "client")
        self.assertEqual(self.status(plan)["workspace"]["action"], "prepare")
        self.github.fail_create = True

        with self.assertRaises(release.ReleaseError):
            self.advance(plan)

        self.advance(plan)
        self.advance(plan, merge=True)
        self.advance(plan)
        self.advance(plan, merge=True)
        self.assertEqual(self.status(plan)["workspace"]["stage"], "sync")
        self.advance(plan)
        self.advance(plan, merge=True)
        statuses = self.status(plan)
        self.assertTrue(all(item["stage"] == "complete" for item in statuses.values()), statuses)
        self.assertEqual(git(self.remotes["workspace"], "ls-tree", "master", "client").split()[2], client_revision)
        self.assertEqual(git(self.remotes["workspace"], "ls-tree", "master", "server").split()[2], server_revision)
        self.assertTrue(read_remote(self, "workspace", "master", f"releases/{plan['id']}.json"))
        before = list(self.github.calls)
        self.advance(plan, merge=True)
        self.assertEqual(self.github.calls, before)

    def test_no_merge_without_explicit_flag(self):
        plan = self.plan()
        self.advance(plan, prepare_only=True)
        self.advance(plan)
        self.assertTrue(all(pr["state"] == "OPEN" for pr in self.github.pulls.values()))

    def test_client_rebuild_reuses_published_server(self):
        plan = self.manager.create_plan("1.0.0", 2, "papyrus")
        self.publish(plan, "server")
        self.advance(plan, prepare_only=True)
        self.assertEqual(len(self.github.pulls), 1)
        self.assertEqual(next(iter(self.github.pulls))[0], "test/client")
        self.assertEqual(self.status(plan)["server"]["stage"], "published")

    def test_already_recorded_snapshot_does_not_create_bookkeeping_prs(self):
        plan = self.manager.create_plan("1.0.0", 1, "papyrus")
        revision = self.publish(plan, "server")
        self.publish(plan, "client")
        self.healthy_server(revision)
        self.manager.verify(plan, "server", revision, "verified running digest")
        observations, actions = self.advance(plan, merge=True)
        self.assertTrue(all(item["stage"] == "complete" for item in observations.values()))
        self.assertEqual(actions, [])
        self.assertEqual(self.github.calls, [])

    def test_successful_workflow_with_skipped_delivery_is_not_verified(self):
        plan = self.manager.create_plan("1.0.0", None, "website")
        self.publish(plan, "website")
        original = self.github.data

        def skipped(*args):
            if args[:2] == ("run", "view"):
                return {"jobs": [{"conclusion": "skipped", "steps": []}]}

            return original(*args)

        with patch.object(self.github, "data", side_effect=skipped):
            self.assertEqual(self.status(plan)["website"]["stage"], "published")

    def test_delivery_retry_dispatches_published_tag_without_building(self):
        plan = self.manager.create_plan("1.0.0", 1, "papyrus")
        self.publish(plan, "server")
        self.publish(plan, "client", success=False)
        self.manager.retry(plan, "client", "web")
        self.assertEqual(
            self.github.calls[-1],
            (
                "workflow",
                "run",
                "delivery.yml",
                "--repo",
                "test/client",
                "--ref",
                "master",
                "-f",
                "tag=v1.0.0+1",
                "-f",
                "destination=web",
            ),
        )

    def test_multiple_builds_require_full_release_id(self):
        self.manager.create_plan("1.0.1", 2, "papyrus")
        self.manager.create_plan("1.0.1", 3, "papyrus")

        with self.assertRaisesRegex(release.ReleaseError, "ambiguous"):
            self.manager.load("1.0.1")

        self.assertEqual(self.manager.load("1.0.1+2")["build"], 2)

    def test_rejects_build_number_already_on_master(self):
        self.add_commit("client", "master", "app/pubspec.yaml", "name: papyrus\nversion: 1.0.0+2\n")

        with self.assertRaisesRegex(release.ReleaseError, "exceed master"):
            self.plan()

    def test_base_change_between_observation_and_merge_is_rejected(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        item = self.status(plan)["website"]
        self.add_commit("website", "development", "new.txt", "concurrent change")

        with self.assertRaisesRegex(release.ReleaseError, "changed or blocked"):
            self.manager.execute(plan, item, merge=True)

        self.assertFalse(any(call[:2] == ("pr", "merge") for call in self.github.calls))

    def test_changed_development_blocks_until_explicit_acceptance(self):
        plan = self.plan("website")
        self.add_commit("website", "development", "feature.txt", "new scope")
        self.assertEqual(self.status(plan)["website"]["stage"], "blocked")
        self.manager.accept_head(plan, "website")
        self.assertEqual(self.status(plan)["website"]["action"], "prepare")

    def test_accept_development_after_preparation_pr_is_open(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        self.add_commit("website", "development", "feature.txt", "new scope")
        self.assertEqual(self.status(plan)["website"]["stage"], "blocked")
        self.manager.accept_head(plan, "website")
        self.assertEqual(self.status(plan)["website"]["action"], "merge")

    def test_changed_preparation_head_blocks_merge(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        self.add_commit("website", self.manager.branch(plan), "unexpected.txt", "new scope")
        self.assertEqual(self.status(plan)["website"]["stage"], "blocked")
        before = len(self.github.calls)
        self.advance(plan, merge=True)
        self.assertEqual(before, len(self.github.calls))

    def test_version_and_build_regressions_are_rejected(self):
        for version, build in (("0.9.0", 2), ("1.0.1", 1), ("1.0.1", 0), ("1.0.1", 2100000001)):
            with self.subTest(version=version, build=build), self.assertRaises(release.ReleaseError):
                self.manager.create_plan(version, build, "papyrus")

    def test_verification_cannot_attest_an_unpublished_or_wrong_commit(self):
        plan = self.plan()

        with self.assertRaises(release.ReleaseError):
            self.manager.verify(plan, "server", "a" * 40, "evidence")

        revision = self.promote_server(plan)

        with self.assertRaises(release.ReleaseError):
            self.manager.verify(plan, "server", "a" * 40, "evidence")

        with self.assertRaisesRegex(release.ReleaseError, "Notes cannot override"):
            self.manager.verify(plan, "server", revision, "what")

        self.healthy_server(revision)
        self.manager.verify(plan, "server", revision, "evidence")
        self.assertEqual(self.status(plan)["server"]["stage"], "verified")

    def test_placeholder_attestation_cannot_complete_an_already_synchronized_release(self):
        plan = self.manager.create_plan("1.0.0", 1, "papyrus")
        revision = self.publish(plan, "server")
        self.publish(plan, "client")
        plan["components"]["server"]["verification"] = {"revision": revision, "evidence": "what"}
        observations, actions = self.advance(plan, merge=True)
        self.assertEqual(observations["server"]["stage"], "published")
        self.assertIn("live deployment check required", observations["server"]["detail"])
        self.assertEqual(observations["workspace"]["stage"], "waiting")
        self.assertEqual(actions, [])
        self.assertEqual(self.github.calls, [])

    def test_previous_success_is_rechecked_and_live_failure_prevents_completion(self):
        plan = self.manager.create_plan("1.0.0", 1, "papyrus")
        revision = self.publish(plan, "server")
        self.publish(plan, "client")
        probe = self.healthy_server(revision)
        self.manager.verify(plan, "server", revision, "checked")
        self.assertTrue(all(item["stage"] == "complete" for item in self.status(plan).values()))
        probe.side_effect = release.DeploymentError("live API is 0.0.3, expected 1.0.0")
        self.manager.save(plan)
        reloaded = self.manager.load(plan["id"])
        observations, actions = self.advance(reloaded, merge=True)
        self.assertEqual(observations["server"]["stage"], "published")
        self.assertIn("live API is 0.0.3", observations["server"]["detail"])
        self.assertEqual(actions, [])

        from release_catalog import Catalog

        record = {**reloaded, "plan": reloaded, "source": "local", "updated": "2026-10-24"}

        with patch.object(Catalog, "discover", return_value={plan["id"]: record}):
            rows = Catalog(self.manager).list("papyrus")

        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["status"], "complete")
        self.assertEqual(rows[0]["components"][0]["stage"], "published")

    def test_notes_cannot_override_failed_delivery(self):
        plan = self.manager.create_plan("1.0.0", None, "website")
        revision = self.publish(plan, "website", success=False)
        plan["components"]["website"]["verification"] = {"revision": revision, "evidence": "verified"}
        self.assertEqual(self.status(plan)["website"]["stage"], "published")

        with self.assertRaisesRegex(release.ReleaseError, "Notes cannot override"):
            self.manager.verify(plan, "website", revision, "what")

    def test_existing_tag_cannot_hide_a_different_promotion(self):
        plan = self.plan()
        self.promote_server(plan)
        promotion = self.github.pulls[("test/server", plan["components"]["server"]["prs"]["promote"])]
        promotion["mergeCommit"]["oid"] = "f" * 40
        self.assertEqual(self.status(plan)["server"]["stage"], "blocked")

    def test_website_bump_preserves_dependency_versions(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        lock = json.loads(read_remote(self, "website", self.manager.branch(plan), "package-lock.json"))
        self.assertEqual(lock["version"], "1.0.1")
        self.assertEqual(lock["packages"][""]["version"], "1.0.1")
        self.assertEqual(lock["packages"]["node_modules/dependency"]["version"], "1.0.0")

    def test_retry_only_failed_run_for_exact_release_commit(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        self.advance(plan, merge=True)
        self.advance(plan)
        self.advance(plan, merge=True)
        self.publish(plan, "website", success=False)
        self.manager.retry(plan, "website", "release")
        self.assertEqual(self.github.calls[-1], ("run", "rerun", 123, "--repo", "test/website", "--failed"))

    def test_output_is_one_line_per_component_and_json_is_parseable(self):
        plan = self.plan()
        observations = self.status(plan)

        with contextlib.redirect_stdout(io.StringIO()) as output:
            release.render(plan, observations)

        self.assertEqual(len(output.getvalue().splitlines()), 5)
        self.assertIn("Next: tools/papyrus release prepare papyrus-1.0.1+2", output.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as output:
            release.render(plan, observations, as_json=True)

        self.assertEqual(json.loads(output.getvalue())["release"], plan["id"])

    def test_concurrent_commands_are_rejected_and_lock_is_released(self):
        other = release.Manager(self.root)

        with self.manager.lock():
            with self.assertRaisesRegex(release.ReleaseError, "Another release"):
                with other.lock():
                    pass

        with other.lock():
            pass

    def add_commit(self, name, branch, filename, content):
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "clone"
            git(self.root, "clone", "--quiet", self.remotes[name], clone)
            self.identity(clone)
            git(clone, "checkout", "--quiet", "-B", branch, f"origin/{branch}")
            (clone / filename).write_text(content)
            git(clone, "add", filename)
            git(clone, "commit", "--quiet", "-m", "New work")
            git(clone, "push", "--quiet", "origin", branch)


def read_remote(fixture, name, ref, filename):
    return git(fixture.remotes[name], "show", f"{ref}:{filename}")


class GitHubChecksTest(unittest.TestCase):
    def ready_pr(self, **changes):
        return {
            "number": 1,
            "isDraft": False,
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "backend", "conclusion": "SUCCESS"}],
            **changes,
        }

    def resolved_threads(self):
        return [{"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}]

    def test_clean_merge_state_respects_github_review_exemptions(self):
        github = release.GitHub(Path("."))

        with patch.object(github, "data", return_value=self.resolved_threads()):
            self.assertIsNone(github.merge_blocker("test/repo", self.ready_pr()))

    def test_review_flag_does_not_hide_running_or_failed_ci(self):
        github = release.GitHub(Path("."))
        pr = self.ready_pr(
            mergeStateStatus="BLOCKED",
            statusCheckRollup=[
                {"name": "windows", "status": "IN_PROGRESS"},
                {"name": "Code quality checks", "status": "QUEUED"},
                {"name": "Compile Linux release", "status": "IN_PROGRESS"},
            ],
        )

        with patch.object(github, "data") as data:
            self.assertEqual(github.merge_blocker("test/repo", pr), "CI running: windows, Code quality checks (+1)")
            pr["statusCheckRollup"].append({"name": "backend", "conclusion": "FAILURE"})
            self.assertEqual(github.merge_blocker("test/repo", pr), "CI failed: backend")
            data.assert_not_called()

    def test_github_blocked_or_unknown_states_still_prevent_merge(self):
        github = release.GitHub(Path("."))

        with patch.object(github, "data", return_value=self.resolved_threads()):
            for state in ("BLOCKED", "UNKNOWN", "DIRTY", "BEHIND"):
                with self.subTest(state=state):
                    self.assertIsNotNone(github.merge_blocker("test/repo", self.ready_pr(mergeStateStatus=state)))

    def test_review_exemptions_do_not_ignore_requested_changes_or_unresolved_threads(self):
        github = release.GitHub(Path("."))
        requested = self.ready_pr(reviewDecision="CHANGES_REQUESTED")
        self.assertIn("changes requested", github.merge_blocker("test/repo", requested))
        pages = self.resolved_threads()
        pages[0]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"] = [{"isResolved": False}]

        with patch.object(github, "data", return_value=pages):
            self.assertEqual(github.merge_blocker("test/repo", self.ready_pr()), "unresolved review threads")

    def test_status_and_dry_run_show_the_next_command(self):
        plan = {"id": "papyrus-0.1.0+5"}
        observations = {
            "server": release.step(
                "server", "prepare", "ready to merge https://github.com/test/server/pull/45", "merge"
            ),
            "client": release.step("client", "prepare", "CI running: windows", blocker="CI running: windows"),
            "workspace": release.step("workspace", "waiting", "after server and client releases are verified"),
        }

        with contextlib.redirect_stdout(io.StringIO()) as output:
            release.render(plan, observations, dry_run=True, merge=True)

        self.assertIn("would merge preparation PR", output.getvalue())
        self.assertIn("Next: tools/papyrus release advance papyrus-0.1.0+5 --merge", output.getvalue())
        self.assertNotIn("approval", output.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as output:
            release.render(plan, observations, dry_run=True)

        self.assertIn("Dry run: ready PRs need --merge.", output.getvalue())
        self.assertNotIn("would merge", output.getvalue())

    def test_status_without_ready_actions_gives_actionable_ci_guidance(self):
        plan = {"id": "papyrus-0.1.0+5"}
        observations = {
            "client": release.step("client", "prepare", "CI running: windows", blocker="CI running: windows")
        }
        self.assertEqual(
            release.next_step(plan, observations),
            "Wait for CI, then rerun: tools/papyrus release status papyrus-0.1.0+5",
        )

        with contextlib.redirect_stdout(io.StringIO()) as output:
            release.render(plan, observations, as_json=True)

        self.assertEqual(json.loads(output.getvalue())["next"], release.next_step(plan, observations))

    def test_pending_failed_reviews_and_paginated_unresolved_threads_block(self):
        github = release.GitHub(Path("."))
        pr = {
            "number": 1,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }
        resolved = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{"isResolved": True}]}}}}}
        unresolved = copy.deepcopy(resolved)
        unresolved["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["isResolved"] = False

        with patch.object(github, "data", return_value=[resolved, unresolved]):
            self.assertEqual(github.merge_blocker("test/repo", pr), "unresolved review threads")

        with patch.object(github, "data", return_value=[resolved]):
            self.assertIsNone(github.merge_blocker("test/repo", pr))

        for change in (
            {"reviewDecision": "CHANGES_REQUESTED"},
            {"isDraft": True},
            {"statusCheckRollup": []},
            {"statusCheckRollup": [{"status": "IN_PROGRESS"}]},
            {"statusCheckRollup": [{"conclusion": "FAILURE"}]},
        ):
            self.assertIsNotNone(github.merge_blocker("test/repo", {**pr, **change}))

    def test_network_errors_are_not_treated_as_missing_releases(self):
        github = release.GitHub(Path("."))
        failure = subprocess.CompletedProcess([], 1, "", "authentication failed (HTTP 401)")

        with patch.object(release, "command", return_value=failure):
            with self.assertRaisesRegex(release.ReleaseError, "401"):
                github.api("repos/test/repo/releases/tags/v1.0.0", missing=True)

    def test_main_reports_errors_without_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as errors:
            result = release.main(["status", "../../escape"], root=Path(directory))

        self.assertEqual(result, 1)
        self.assertEqual(len(errors.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
