"""The deployment receipt must correspond to a live request and an independently restored archive."""

import contextlib
import fcntl
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_backup_host as host
import release_backup_mac as mac


class BackupTest(unittest.TestCase):
    def test_stale_job_is_not_offered_and_wrong_receipt_is_rejected(self):
        self.addCleanup(os.umask, os.umask(0o077))
        with tempfile.TemporaryDirectory() as directory, patch.object(host, "SPOOL", Path(directory)):
            job = host.SPOOL / ("a" * 32)
            job.mkdir()
            manifest = {"id": job.name, "sha256": "b" * 64}
            host.write_json(job / "ready.json", manifest)

            with (job / "active.lock").open("w") as lease:
                self.assertIsNone(host.pending())
                fcntl.flock(lease, fcntl.LOCK_EX)
                self.assertEqual(host.pending(), manifest)
                bad = json.dumps(manifest | {"sha256": "c" * 64, "snapshot": "d" * 64})
                with (
                    patch("sys.stdin", io.StringIO(bad)),
                    self.assertRaisesRegex(ValueError, "Invalid restore"),
                ):
                    host.main(["confirm", job.name])
                self.assertFalse((job / "verified.json").exists())
                good = json.dumps(manifest | {"snapshot": "d" * 64})
                with patch("sys.stdin", io.StringIO(good)):
                    host.main(["confirm", job.name])
                self.assertIsNone(host.pending())

    def test_corrupt_readback_cannot_reach_database_restore(self):
        content = b"wrong"
        manifest = {"bytes": len(content), "sha256": hashlib.sha256(b"right").hexdigest()}

        def dump(*args, stdout, **kwargs):
            stdout.write(content)

        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(mac, "DESTINATION", Path(directory)))
            stack.enter_context(patch.object(mac.subprocess, "run", side_effect=dump))
            restore = stack.enter_context(patch.object(mac, "restore_database"))
            with self.assertRaisesRegex(ValueError, "differs from the host"):
                mac.verify(manifest, "fake snapshot")
            restore.assert_not_called()
