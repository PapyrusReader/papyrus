import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "octopus"))
import pipeline


class PipelineTests(unittest.TestCase):
    def test_manifest_is_pinned_and_new_development_commits_do_not_change_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clone = root / "workspace"
            clone.mkdir()

            def git(repo, *args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

            def init(repo):
                repo.mkdir(exist_ok=True)
                git(repo, "init", "-qb", "development")
                git(repo, "config", "user.name", "Papyrus Test")
                git(repo, "config", "user.email", "test@papyrus.test")
                git(repo, "config", "commit.gpgsign", "false")

            init(clone)
            (clone / "release.json").write_text('{"version":"0.6.0","android_build_number":10}')
            git(clone, "add", "release.json")
            git(clone, "commit", "-qm", "Baseline")
            git(clone, "branch", "master")
            git(clone, "remote", "add", "origin", str(clone))
            reader = "c" * 40
            for name in ("client", "server"):
                repo = clone / name
                init(repo)
                if name == "client":
                    (repo / "app").mkdir()
                    (repo / "app/pubspec.yaml").write_text("version: 0.6.0+10\n")
                    (repo / "app/pubspec.lock").write_text(f'packages:\n  papyrus_reader:\n    description:\n      resolved-ref: "{reader}"\n')
                else:
                    (repo / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
                    (repo / "uv.lock").write_text('[[package]]\nname = "papyrus-server"\nversion = "0.6.0"\n')
                git(repo, "add", ".")
                git(repo, "commit", "-qm", "Reviewed")
                git(repo, "remote", "add", "origin", str(repo))

            with patch.object(pipeline, "ROOT", clone):
                plan = pipeline.snapshot("0.7.0+11")
                self.assertEqual(plan, pipeline.snapshot("0.7.0+11"))
                git(clone / "client", "commit", "--allow-empty", "-qm", "Later feature")
                self.assertNotEqual(plan["sources"]["client"], git(clone / "client", "rev-parse", "HEAD"))
                revision, tag = pipeline.resolve(clone, plan["head"], "client", plan["head"])
                self.assertEqual((revision, tag), (plan["pins"]["client"], "v0.7.0+11"))
                self.assertEqual(git(clone, "rev-parse", plan["head"] + ":reader"), reader)
                with self.assertRaises(ValueError):
                    pipeline.snapshot("0.7.0+10")

                # GitHub creates the approval merge in a separate repository from the release host.
                remote = root / "github"
                git(clone, "branch", "octopus/0.7.0+11", plan["head"])
                git(root, "clone", "--quiet", "--no-local", "--branch", "master", str(clone), str(remote))
                git(remote, "-c", "user.name=Reviewer", "-c", "user.email=test@papyrus.test",
                    "merge", "--no-gpg-sign", "--no-ff", "--no-edit", "origin/octopus/0.7.0+11")
                merge = git(remote, "rev-parse", "HEAD")
                git(clone, "remote", "set-url", "origin", str(remote))
                pipeline.save(root / "plan.json", plan)
                hub = Mock()
                hub.call.return_value = " ".join(("def resolve(", "Build client ${{ inputs.manifest }}",
                                                  "Build server ${{ inputs.manifest }}", "sealed-release"))
                hub.find_pr.return_value = {"url": "https://example.test/pr/1", "number": 1}
                hub.pr.return_value = {"state": "MERGED", "headRefOid": plan["head"], "mergeCommit": {"oid": merge}}
                pipeline.prepare(hub, root, "0.7.0+11")
                self.assertEqual(json.loads((root / "manifest.json").read_text()), merge)

    def test_server_job_receives_the_approved_manifest_over_ssh(self):
        request = {"version": "0.7.0", "revision": "a" * 40, "manifest": "b" * 40,
                   "image": "ghcr.io/papyrusreader/server@sha256:" + "c" * 64}
        with patch.object(pipeline.subprocess, "run") as ssh:
            ssh.return_value.returncode = 0
            pipeline.invoke_server_job({"ssh_host": "fixture"}, request)
            self.assertEqual(ssh.call_args.args[0][-3:], ["--", "fixture", "/usr/local/bin/papyrus-release-server"])
            self.assertEqual(json.loads(ssh.call_args.kwargs["input"]), request)
            ssh.return_value.returncode = 1
            with self.assertRaises(pipeline.DeploymentError):
                pipeline.invoke_server_job({"ssh_host": "fixture"}, request)

    def test_play_retry_uses_original_run_and_never_rebuilds_or_dispatches_twice(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline.time, "sleep"):
            directory = Path(tmp)
            hub = Mock()
            title = "Deliver v0.7.0+11 to play"
            run = {"display_title": title, "head_branch": "master", "path": ".github/workflows/delivery.yml",
                   "status": "completed", "run_attempt": 1, "conclusion": "failure"}
            success = dict(run, run_attempt=2, conclusion="success")
            hub.data.side_effect = [{"workflow_run_id": 123}, {"jobs": [{"name": "play", "conclusion": "success"}]}]
            hub.api.side_effect = [None, dict(run, display_title="Delivery"), run, run, success]
            args = hub, directory, "play", "client", "delivery.yml", title, {"tag": "v0.7.0+11", "destination": "play"}
            with self.assertRaisesRegex(ValueError, "retry this Octopus step"):
                pipeline.run_workflow(*args)
            pipeline.run_workflow(*args)
            self.assertEqual(json.loads((directory / "play.json").read_text())["id"], 123)
            hub.call.assert_called_once_with("api", "--method", "POST", "repos/PapyrusReader/client/actions/runs/123/rerun-failed-jobs")
            self.assertEqual(sum("dispatches" in str(call) for call in hub.data.call_args_list), 1)
            for poll in hub.api.call_args_list:
                self.assertEqual(poll.args, ("repos/PapyrusReader/client/actions/runs/123",))
                self.assertEqual(poll.kwargs, {"missing": True})

    def test_persistent_identity_mismatch_keeps_recorded_run_for_inspection(self):
        title = "Publish manifest"
        run = {"display_title": title, "head_branch": "master", "path": ".github/workflows/publish.yml",
               "status": "completed", "conclusion": "success", "run_attempt": 1}
        for field, wrong in (("display_title", "Publish another manifest"), ("head_branch", "development"),
                             ("path", ".github/workflows/delivery.yml")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp, patch.object(pipeline.time, "sleep") as sleep:
                directory = Path(tmp)
                pipeline.save(directory / "publish.json", {"id": 123})
                hub = Mock()
                hub.api.return_value = {**run, field: wrong}
                with self.assertRaises(ValueError) as error:
                    pipeline.run_workflow(hub, directory, "publish", "client", "publish.yml", title, {})
                self.assertIn("runs/123", str(error.exception))
                self.assertIn(wrong, str(error.exception))
                self.assertIn(run[field], str(error.exception))
                self.assertEqual(hub.api.call_count, pipeline.RUN_IDENTITY_ATTEMPTS)
                self.assertEqual(sleep.call_count, pipeline.RUN_IDENTITY_ATTEMPTS - 1)
                hub.data.assert_not_called()
                hub.call.assert_not_called()
                self.assertEqual(json.loads((directory / "publish.json").read_text()), {"id": 123})
