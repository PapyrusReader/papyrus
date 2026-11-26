"""Exercise release recovery and ordering with real Git repositories and fake GitHub."""

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import release_manager as release  # noqa: E402
from release_runner import Runner  # noqa: E402


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
        return self.merge_gate(repo, pr)[1]

    def merge_gate(self, repo, pr):
        if "test_gate" in pr:
            return pr["test_gate"]

        if pr["mergeStateStatus"] == "CLEAN":
            return None, None

        return "ci_pending", "checks pending or failing"

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
            body = args[args.index("--body") + 1] if "--body" in args else Path(args[args.index("--body-file") + 1]).read_text()
            self.pulls[(repo, number)] = {
                "number": number,
                "url": f"https://github.com/{repo}/pull/{number}",
                "state": "OPEN",
                "body": body,
                "headRefName": head,
                "baseRefName": base,
                "headRefOid": self.ref(repo, head),
                "baseRefOid": self.ref(repo, base),
                "mergeCommit": None,
                "isDraft": "--draft" in args,
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [],
            }
            return self.pulls[(repo, number)]["url"]

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
                "attempt": 1,
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

    def runner(self, plan, *, deploy=None, notify=None, **options):
        defaults = {
            "release": plan["id"], "merge": True, "repair": False, "dry_run": False,
            "poll_seconds": 1, "wait_timeout": None, "deploy_server": None,
        }
        report = []
        dependencies = {"deploy": deploy} if deploy else {}
        runner = Runner(
            self.manager, SimpleNamespace(**(defaults | options)),
            lambda release_id, decision, observations: report.append((decision.state, decision.detail)),
            notify=notify, **dependencies,
        )
        return runner, report

    def test_runner_waits_deploys_and_completes_the_saved_release(self):
        plan = self.plan()
        published = {}
        deployed = []
        target = {"ssh_host": "fixture"}
        digest = "sha256:" + "a" * 64

        def deploy(selected, executable, request):
            self.assertEqual(selected, target)
            self.assertEqual(executable, "/opt/papyrus/deploy")
            self.assertEqual(self.status(self.manager.load(plan["id"]))["client"]["stage"], "waiting")
            self.assertEqual(request, {
                "version": plan["version"], "revision": published["server"],
                "image": f"ghcr.io/papyrusreader/server@{digest}",
            })
            deployed.append(request["revision"])
            return "Deployment job finished"

        def probe(plan, revision):
            if revision not in deployed:
                raise release.ServerVersionMismatch("1.0.0", plan["version"])

            return {"revision": revision}

        def publish_next(_seconds):
            repo = plan["components"]["server"]["repo"]

            if "server" not in published:
                published["server"] = self.publish(plan, "server")
                self.github.runs[repo, published["server"]][0].update(status="in_progress", conclusion=None)
            elif not deployed:
                self.github.runs[repo, published["server"]][0].update(status="completed", conclusion="success")
            else:
                self.assertNotIn("client", published)
                published["client"] = self.publish(plan, "client")

        runner, report = self.runner(plan, deploy=deploy, deploy_server="/opt/papyrus/deploy")
        image_record = f"ghcr.io/papyrusreader/server:{plan['version']}\nDigest {digest}\n"

        with (
            patch.object(self.manager, "check_server_deployment", side_effect=probe),
            patch.object(self.manager, "deployment_target", return_value=target),
            patch.object(self.manager, "server_image_record", return_value=image_record),
            patch("release_runner.time", wraps=time) as runner_clock,
        ):
            runner_clock.sleep.side_effect = publish_next
            self.assertEqual(runner.run(), 0)
            self.assertEqual(runner.run(), 0)

        self.assertEqual(deployed, [published["server"]])
        self.assertIn(("waiting", "server: waiting for the publication workflow to finish"), report)
        self.assertEqual(report[-1][0], "complete")
        attempt = self.manager.load(plan["id"])["automation"][f"deploy:{published['server']}"]
        self.assertEqual(attempt["state"], "finished")
        self.assertEqual(git(self.remotes["workspace"], "ls-tree", "master", "client").split()[2], published["client"])

    def test_runner_dry_run_and_merge_opt_in(self):
        plan = self.plan("website")
        notify = Mock()
        runner, report = self.runner(plan, dry_run=True, notify=notify)
        self.assertEqual(runner.run(), 0)
        self.assertEqual(report[-1][0], "preview")
        self.assertEqual(self.github.calls, [])
        notify.assert_not_called()
        runner, report = self.runner(plan, merge=False, notify=notify)
        self.assertEqual(runner.run(), 2)
        self.assertIn("--merge", report[-1][1])
        self.assertEqual([call[:2] for call in self.github.calls], [("pr", "create")])
        notify.assert_called_once_with(plan["id"], "blocked", report[-1][1])

    def test_runner_does_not_repeat_an_interrupted_server_deploy(self):
        plan = self.plan()
        revision = self.promote_server(plan)
        plan["automation"] = {f"deploy:{revision}": {"state": "started"}}
        self.manager.save(plan)
        digest = "sha256:" + "a" * 64
        image_record = f"ghcr.io/papyrusreader/server:{plan['version']}\nDigest {digest}\n"
        deployment = Mock()

        with (
            patch.object(self.manager, "check_server_deployment", side_effect=release.ServerVersionMismatch("1.0.0", "1.0.1")),
            patch.object(self.manager, "deployment_target", return_value={}),
            patch.object(self.manager, "server_image_record", return_value=image_record),
        ):
            runner, report = self.runner(plan, deploy=deployment, deploy_server="/opt/papyrus/deploy")
            self.assertEqual(runner.run(), 2)

        self.assertIn("was interrupted", report[-1][1])
        deployment.assert_not_called()

    def test_runner_retries_a_timed_out_release_workflow_once(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        self.advance(plan, merge=True)
        self.advance(plan)
        self.advance(plan, merge=True)
        revision = self.publish(plan, "website", success=False)
        repo = plan["components"]["website"]["repo"]
        run = self.github.runs[repo, revision][0]
        run["conclusion"] = "timed_out"
        runner, _ = self.runner(plan)
        _, _, decision = runner.tick()
        self.assertEqual(decision.state, "progress")
        _, _, decision = runner.tick()
        self.assertEqual(decision.state, "waiting")
        run["attempt"] = 2
        _, _, decision = runner.tick()
        self.assertEqual(decision.state, "blocked")
        retries = [call for call in self.github.calls if call[:2] == ("run", "rerun")]
        self.assertEqual(retries, [("run", "rerun", 123, "--repo", repo, "--failed")])

    def test_failed_check_gets_one_draft_repair_on_the_recorded_head(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        repo = plan["components"]["website"]["repo"]
        pr = next(iter(self.github.pulls.values()))
        pr["test_gate"] = ("ci_failed", "CI failed: test")
        pr["statusCheckRollup"] = [{"conclusion": "FAILURE", "name": "test"}]
        runner, _ = self.runner(plan, repair=True)

        def edit(worktree, prompt):
            self.assertIn(pr["headRefOid"], prompt)
            self.assertIn("CI failed: test", prompt)
            (worktree / "fix.txt").write_text("fixed\n")

        with patch("release_repair.edit_with_codex", side_effect=edit) as codex:
            loaded, _, decision = runner.tick()
            self.assertEqual(decision.state, "working")
            finished = runner.finish_job(loaded["id"], *decision.job)
            self.assertEqual(finished.state, "progress", finished.detail)
            restarted, _ = self.runner(plan, repair=True)
            _, _, waiting = restarted.tick()
            self.assertEqual(waiting.state, "waiting")
            self.assertIn("Codex repair", waiting.detail)
            codex.assert_called_once()

        draft = self.github.pulls[repo, 2]
        self.assertTrue(draft["isDraft"])
        self.assertEqual(draft["baseRefName"], pr["headRefName"])
        self.assertEqual(git(self.remotes["website"], "rev-parse", f"{draft['headRefName']}^"), pr["headRefOid"])
        self.assertEqual(read_remote(self, "website", draft["headRefName"], "fix.txt").strip(), "fixed")

    def test_conflict_repair_preserves_both_recorded_histories(self):
        path = self.manager.path("website")

        for branch in ("master", "development"):
            git(path, "checkout", branch)
            (path / "conflict.txt").write_text(f"{branch}\n")
            git(path, "add", "conflict.txt")
            git(path, "commit", "-m", f"Change {branch}")
            git(path, "push", "origin", branch)

        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        self.advance(plan, merge=True)
        self.advance(plan)
        repo = plan["components"]["website"]["repo"]
        promotion = self.github.pulls[repo, 2]
        promotion["test_gate"] = ("conflicts", "merge conflicts")
        runner, _ = self.runner(plan, repair=True)

        def edit(worktree, _prompt):
            self.assertIn("<<<<<<<", (worktree / "conflict.txt").read_text())
            (worktree / "conflict.txt").write_text("resolved\n")

        with patch("release_repair.edit_with_codex", side_effect=edit):
            loaded, _, decision = runner.tick()
            self.assertEqual(decision.state, "working")
            finished = runner.finish_job(loaded["id"], *decision.job)
            self.assertEqual(finished.state, "progress", finished.detail)

        draft = self.github.pulls[repo, 3]
        self.assertTrue(draft["isDraft"])
        self.assertEqual(draft["baseRefName"], "development")
        parents = git(self.remotes["website"], "log", "-1", "--format=%P", draft["headRefName"]).split()
        self.assertEqual(parents, [promotion["headRefOid"], promotion["baseRefOid"]])
        self.assertEqual(read_remote(self, "website", draft["headRefName"], "conflict.txt").strip(), "resolved")

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

    def test_mutations_require_a_local_plan(self):
        plan = self.plan()
        archived = self.root / "releases" / f"{plan['id']}.json"
        archived.parent.mkdir()
        (self.manager.storage / archived.name).rename(archived)

        with self.assertRaisesRegex(release.ReleaseError, "Release not found"):
            self.manager.load(plan["id"])

        self.manager.save(plan)
        self.assertEqual(self.manager.load(plan["id"]), plan)

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
        self.assertEqual(
            json.loads(read_remote(self, "workspace", "master", "release.json")),
            {"version": plan["version"], "android_build_number": plan["build"]},
        )
        self.assertEqual(git(self.remotes["workspace"], "ls-tree", "master", "releases"), "")
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

    def test_runner_follows_merged_development_pr_and_recovers_after_push(self):
        plan = self.plan()
        self.advance(plan, prepare_only=True)
        branch = self.manager.branch(plan)
        saved = copy.deepcopy(plan)
        self.add_commit("client", "development", "chart.txt", "fixed")
        base = git(self.remotes["client"], "rev-parse", "development")
        self.github.releases[f"repos/test/client/commits/{base}/pulls"] = [{
            "merged_at": "today", "merge_commit_sha": base,
            "base": {"ref": "development", "repo": {"full_name": "test/client"}},
        }]
        for pr in self.github.pulls.values():
            pr["test_gate"] = ("ci_pending", "CI running")

        runner, _ = self.runner(plan)
        self.assertEqual(runner.tick()[2].state, "progress")
        head = git(self.remotes["client"], "rev-parse", branch)
        self.assertEqual(git(self.remotes["client"], "show", "-s", "--format=%P", head),
                         f"{saved['components']['client']['prepared_head']} {base}")
        self.assertEqual(git(self.remotes["client"], "diff", "--name-only", base, head), "app/pubspec.yaml")

        # Simulate a restart after a push, before the new pair of SHAs was persisted.
        self.manager.save(saved)
        self.assertEqual(runner.tick()[2].state, "progress")
        self.assertEqual(runner.tick()[2].state, "waiting")
        component = self.manager.load(plan["id"])["components"]["client"]
        self.assertEqual((component["accepted_development"], component["prepared_head"]), (base, head))
        self.assertEqual(git(self.remotes["client"], "rev-parse", branch), head)

    def test_runner_preserves_unexpected_development_and_preparation_changes(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        saved = copy.deepcopy(plan)
        self.add_commit("website", "development", "direct.txt", "direct push")
        runner, _ = self.runner(plan)
        with patch.object(self.github, "api", return_value=[]):
            self.assertEqual(runner.tick()[2].state, "blocked")
        self.assertEqual(self.manager.load(plan["id"]), saved)

        self.manager.accept_head(plan, "website")
        self.add_commit("website", self.manager.branch(plan), "unexpected.txt", "new scope")
        self.assertEqual(runner.tick()[2].state, "blocked")
        # Explicit acceptance can inspect a PR commit created outside this checkout.
        self.manager.accept_head(plan, "website")
        self.assertEqual(plan["components"]["website"]["prepared_head"],
                         git(self.remotes["website"], "rev-parse", self.manager.branch(plan)))

    def test_updated_preparation_with_expected_parents_but_extra_content_stays_blocked(self):
        plan = self.plan("website")
        self.advance(plan, prepare_only=True)
        branch = self.manager.branch(plan)
        component = plan["components"]["website"]
        previous = component["prepared_head"]
        self.add_commit("website", "development", "feature.txt", "integrated")
        self.status(plan)
        self.manager.accept_head(plan, "website")
        base = component["accepted_development"]

        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "clone"
            git(self.root, "clone", "--quiet", self.remotes["website"], clone)
            self.identity(clone)
            git(clone, "checkout", "--quiet", branch)
            git(clone, "merge", "--no-ff", "--no-commit", base)
            (clone / "extra.txt").write_text("outside the base update")
            git(clone, "add", "extra.txt")
            git(clone, "commit", "--quiet", "-m", "Update branch")
            git(clone, "push", "--quiet", "origin", branch)

        runner, _ = self.runner(plan)
        self.assertEqual(runner.tick()[2].state, "blocked")
        self.assertEqual(self.manager.load(plan["id"])["components"]["website"]["prepared_head"], previous)

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
