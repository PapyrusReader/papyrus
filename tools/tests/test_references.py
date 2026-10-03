"""Validate recorded dependency pins independently of dirty working trees."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "references", Path(__file__).resolve().parents[1] / "check_references.py"
)
references = importlib.util.module_from_spec(spec)
spec.loader.exec_module(references)


class CommittedReferencesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.command(self.root, "init", "-q")
        for name in ("client", "reader"):
            self.command(self.root / name, "init", "-q")
        (self.root / "reader/example").mkdir()
        (self.root / "reader/example/fixture").write_text("reader")
        self.commit(self.root / "reader")
        self.reader = self.command(self.root / "reader", "rev-parse", "HEAD")
        (self.root / "client/app").mkdir()
        self.pin(self.reader)
        self.commit(self.root / "client")
        self.record()

    def command(self, root, *args):
        root.mkdir(parents=True, exist_ok=True)
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

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

    def pin(self, revision):
        (self.root / "client/app/pubspec.yaml").write_text(
            f"dependencies:\n  papyrus_reader:\n    git:\n      ref: {revision}\n"
        )
        (self.root / "client/app/pubspec.lock").write_text(
            f'packages:\n  papyrus_reader:\n    description:\n      resolved-ref: "{revision}"\n'
        )

    def record(self):
        for name in ("client", "reader"):
            revision = self.command(self.root / name, "rev-parse", "HEAD")
            self.command(
                self.root,
                "update-index",
                "--add",
                "--cacheinfo",
                "160000",
                revision,
                name,
            )
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
            "workspace",
        )

    def test_dirty_dependency_files_do_not_override_committed_agreement(self):
        self.pin("f" * 40)
        references.check(self.root)

    def test_new_committed_client_requires_matching_reader_pin(self):
        self.pin("f" * 40)
        self.commit(self.root / "client")
        self.record()
        with self.assertRaisesRegex(ValueError, "must match workspace reader"):
            references.check(self.root)

    def test_lock_drift_is_rejected_even_when_manifest_agrees(self):
        (self.root / "client/app/pubspec.lock").write_text(
            'packages:\n  papyrus_reader:\n    description:\n      resolved-ref: "'
            + "f" * 40
            + '"\n'
        )
        self.commit(self.root / "client")
        self.record()
        with self.assertRaisesRegex(ValueError, "resolved-ref must match"):
            references.check(self.root)
