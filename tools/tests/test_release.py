"""Check coordinated version updates and fail before writing invalid bumps."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("coordinated_release", Path(__file__).resolve().parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "client/app").mkdir(parents=True)
        (self.root / "server").mkdir()
        (self.root / "release.json").write_text(json.dumps({"version": "1.0.0", "android_build_number": 1}))
        (self.root / "client/app/pubspec.yaml").write_text("version: 1.0.0+1\ndependencies: unchanged\n")
        (self.root / "server/pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
        (self.root / "server/uv.lock").write_text(
            '[[package]]\nname = "papyrus-server"\nversion = "1.0.0"\n[[package]]\nname = "other"\nversion = "1.0.0"\n'
        )

    def test_bump_updates_only_owned_version_metadata(self):
        release.bump(self.root, "1.0.1", 2)
        manifest, client, server, lock = release.state(self.root)
        self.assertEqual(manifest, {"version": "1.0.1", "android_build_number": 2})
        self.assertIn("dependencies: unchanged", client)
        self.assertIn('name = "other"\nversion = "1.0.0"', lock)

    def test_client_only_rebuild_keeps_server_version(self):
        release.bump(self.root, "1.0.0", 2)
        self.assertEqual(release.state(self.root)[0]["android_build_number"], 2)

    def test_rejected_bumps_do_not_write_any_files(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        for version, number in (("0.9.0", 2), ("1.0.1", 1), ("1.0.1", 2100000001), ("latest", 2)):
            with self.assertRaises(ValueError):
                release.bump(self.root, version, number)
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_drift_is_rejected(self):
        (self.root / "server/pyproject.toml").write_text('[project]\nversion = "1.0.1"\n')
        with self.assertRaisesRegex(ValueError, "Server package"):
            release.state(self.root)

    def command(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, root):
        self.command(root, "add", ".")
        self.command(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        )

    def test_committed_check_uses_recorded_submodules(self):
        for component in ("client", "server"):
            directory = self.root / component
            self.command(directory, "init", "-q")
            self.commit(directory)
        self.command(self.root, "init", "-q")
        for component in ("client", "server"):
            revision = self.command(self.root / component, "rev-parse", "HEAD")
            self.command(self.root, "update-index", "--add", "--cacheinfo", "160000", revision, component)
        self.command(self.root, "add", "release.json")
        self.command(
            self.root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        )
        (self.root / "server/pyproject.toml").write_text('[project]\nversion = "1.0.1"\n')
        self.commit(self.root / "server")
        release.state(self.root, committed=True)
        with self.assertRaises(ValueError):
            release.state(self.root)
