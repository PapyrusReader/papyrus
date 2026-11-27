import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "octopus"))
import entry
import pipeline
import promotion


class GitHubFixture:
    """Implement PR state and actual Git merges. Only the network boundary is simulated."""

    def __init__(self, repos):
        self.repos = repos
        self.prs = {}
        self.reviews = {}
        self.failed = None

    def find_pr(self, name, head, base, marker):
        return next((pr for (repo, _), pr in self.prs.items() if repo == name and pr["headRefName"] == head
                     and pr["baseRefName"] == base and pr["body"] == marker), None)

    def pr(self, repo, number):
        return self.prs[(repo, number)]

    def merge_gate(self, name, pr):
        if self.failed == (name, pr["baseRefName"]):
            return "ci_failed", "fixture failed CI"
        if (name, pr["headRefOid"]) not in self.reviews:
            return "review_pending", "approval required"
        return None, None

    def api(self, endpoint):
        _, org, name, _, number = endpoint.split("/")
        pr = self.pr(f"{org}/{name}", int(number))
        return {"state": "open", "draft": False, "user": {"login": "author"},
                "head": {"ref": pr["headRefName"], "sha": pr["headRefOid"], "repo": {"full_name": f"{org}/{name}"}},
                "base": {"ref": pr["baseRefName"]}}

    def data(self, *args):
        return [[]]

    def call(self, *args):
        if args[:2] == ("api", "user"):
            return "reviewer"
        if args[0] == "api":
            _, org, name, _, number, _ = args[1].split("/")
            sha = next(arg.removeprefix("commit_id=") for arg in args if arg.startswith("commit_id="))
            self.reviews[(f"{org}/{name}", sha)] = int(number)
            return
        name = args[args.index("--repo") + 1]
        repo = self.repos[name.split("/")[-1]]
        if args[1] == "create":
            head = args[args.index("--head") + 1]
            number = len(self.prs) + 1
            self.prs[(name, number)] = dict(number=number, url=f"https://example.test/{number}", headRefName=head,
                                            headRefOid=promotion.fetch(repo, head), baseRefName=args[args.index("--base") + 1],
                                            body=Path(args[args.index("--body-file") + 1]).read_text().strip(), state="OPEN")
            return
        if args[1] != "merge":
            raise AssertionError(args)
        pr = self.pr(name, args[2])
        sha = pr["headRefOid"]
        assert sha == args[-1] and (name, sha) in self.reviews
        base = pr["baseRefName"]
        parent = promotion.fetch(repo, base)
        merge = promotion.git(repo, "commit-tree", f"{sha}^{{tree}}", "-p", parent, "-p", sha, "-m", "Merge PR")
        promotion.git(repo, "update-ref", f"refs/heads/{base}", merge, parent)
        promotion.git(repo, "update-ref", "-d", f"refs/heads/{pr['headRefName']}", sha)
        pr.update(state="MERGED", mergeCommit={"oid": merge})


class ReleasePromotionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.repos = {}
        self.reader = "c" * 40
        files = {
            "client": {"app/pubspec.yaml": "version: 0.6.0+10\n",
                       "app/pubspec.lock": f'packages:\n  papyrus_reader:\n    description:\n      resolved-ref: "{self.reader}"\n'},
            "server": {"pyproject.toml": '[project]\nversion = "0.6.0"\n',
                       "uv.lock": '[[package]]\nname = "papyrus-server"\nversion = "0.6.0"\n'},
            "papyrus": {"release.json": '{"version":"0.6.0","android_build_number":10}\n'},
        }
        for name, contents in files.items():
            repo = self.root / name
            repo.mkdir()
            self.repos[name] = repo
            git = lambda *args: promotion.git(repo, *args)
            git("init", "-qb", "development")
            git("config", "user.name", "Release Test")
            git("config", "user.email", "test@example.test")
            git("config", "commit.gpgsign", "false")
            for path, content in contents.items():
                target = repo / path
                target.parent.mkdir(exist_ok=True)
                target.write_text(content)
            git("add", ".")
            git("commit", "-qm", "baseline")
            git("branch", "master")
            git("remote", "add", "origin", str(repo))
            # Keep worktree detached while the fixture advances remote refs.
            git("checkout", "--quiet", "--detach")
        self.workspace = self.repos["papyrus"]
        self.number = "0.7.0+11"
        self.release = promotion.Promotion(self.workspace, {k: self.repos[k] for k in promotion.COMPONENTS},
                                           self.number, promotion.fetch(self.workspace, "master"))
        self.hub = GitHubFixture(self.repos)
        self.release.hub = self.hub
        self.release.request_review = lambda repo, pr, head: self.release.review_pr(repo.split("/")[-1], pr, head)
        self.enterContext(patch.dict(os.environ, RELEASE_AUTHOR="author"))
        self.enterContext(patch.object(promotion.time, "sleep"))

    def advance(self, name, branch, path):
        repo = self.repos[name]
        parent = promotion.fetch(repo, branch)
        new = promotion.make_commit(repo, parent, path, {path: path + "\n"}, {})
        promotion.git(repo, "update-ref", f"refs/heads/{branch}", new, parent)
        return new

    def test_partial_promotion_resume_delivery_and_sync(self):
        hotfix = self.advance("client", "master", "hotfix")
        self.advance("client", "development", "feature")
        self.hub.failed = ("PapyrusReader/server", "master")
        with self.assertRaisesRegex(ValueError, "failed CI"):
            self.release.promote()
        promoted_client = promotion.fetch(self.repos["client"], "master")
        self.assertTrue(promotion.ancestor(self.repos["client"], hotfix, promoted_client))
        self.assertEqual(self.release.base, promotion.fetch(self.workspace, "master"))
        self.assertEqual(self.release.inputs()["client"], promotion.fetch(self.repos["client"], "development"))
        later = self.advance("server", "development", "later")
        self.hub.failed = None
        manifest = self.release.promote()
        self.assertEqual(self.release.promote(), manifest)
        pins = self.release.verify(manifest)
        self.assertEqual(pins["client"], promoted_client)
        self.assertEqual(pins["server"], promotion.fetch(self.repos["server"], "master"))
        self.assertFalse(promotion.ancestor(self.repos["server"], later, pins["server"]))
        self.assertEqual(promotion.git(self.workspace, "rev-parse", f"{manifest}:reader"), self.reader)
        for name, sha in pins.items():
            self.release.sync(name, sha, "after")
            self.release.sync(name, sha, "after")
        self.assertTrue(promotion.ancestor(self.repos["server"], later, promotion.fetch(self.repos["server"], "development")))
        self.assertEqual(promotion.read_file(self.repos["client"], "development", "app/pubspec.yaml"), "version: 0.7.0+11\n")
        self.assertEqual(len(self.hub.prs), 6)

        with patch.object(pipeline, "ROOT", self.workspace):
            pipeline.prepare(self.root, self.number, manifest)
            pipeline.prepare(self.root, self.number)
            self.assertEqual(pipeline.approved(self.root, self.number), manifest)
            with self.assertRaisesRegex(ValueError, "different workspace manifest"):
                pipeline.prepare(self.root, self.number, self.release.base)

    def test_reviewer_refuses_extra_file_and_version_or_target_mismatch(self):
        sources = self.release.inputs()
        repo = self.repos["client"]
        candidate = self.release.version_commit("client", sources["client"])
        altered = promotion.make_commit(repo, candidate, "unplanned", {"extra": "unexpected"}, {})
        self.hub.prs[("PapyrusReader/client", 1)] = dict(headRefName=self.release.branch, headRefOid=altered,
                                                        baseRefName="master", body="", state="OPEN")
        with self.assertRaisesRegex(ValueError, "independently reconstructed"):
            self.release.review_pr("client", 1, altered)
        self.assertFalse(self.hub.reviews)
        with patch.object(pipeline, "ROOT", self.workspace):
            with self.assertRaisesRegex(ValueError, "manifest SHA"):
                pipeline.prepare(self.root, self.number)
            manifest = self.release.manifest_commit({"client": candidate, "server": sources["server"]})
            promotion.git(self.workspace, "update-ref", "refs/heads/master", manifest, self.release.base)
            with self.assertRaisesRegex(ValueError, "identify"):
                pipeline.prepare(self.root, "0.7.1+12", manifest)
            self.assertFalse((self.root / "manifest.json").exists())

    def test_only_prepare_accepts_a_manifest_argument(self):
        with patch.object(entry, "main") as stage:
            sha = "a" * 40
            entry.dispatch(f"prepare {self.number} {sha}")
            stage.assert_called_once_with("prepare", self.number, sha)
            with self.assertRaises(ValueError):
                entry.dispatch(f"client-build {self.number} {sha}")
