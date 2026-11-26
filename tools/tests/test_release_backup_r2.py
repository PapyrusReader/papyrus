"""A release may advance only after its own downloaded archive passes the restore check."""

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup"))
import release_backup_r2 as backup


class RestoreGateTests(unittest.TestCase):
    def test_failed_restore_preserves_last_verified_receipt(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            contextlib.ExitStack() as patches,
        ):
            root = Path(directory)
            prior = {"key": "previously verified object"}
            previous = root / "last-verified.json"
            previous.write_text(json.dumps(prior))
            for filename in ("recipients", "identity"):
                (root / filename).touch()
            work = root / "capture"
            work.mkdir()
            source = work / "backup.tar"
            source.write_bytes(b"captured")
            patches.enter_context(patch.object(backup, "SPOOL", root))
            patches.enter_context(patch.object(backup, "CONFIG", root))
            patches.enter_context(
                patch.object(backup.shutil, "which", return_value="/bin/age")
            )
            patches.enter_context(patch.object(backup, "snapshot", return_value=source))
            patches.enter_context(
                patch.object(
                    backup.subprocess,
                    "run",
                    side_effect=lambda *a, **kw: (work / "upload.age").touch(),
                )
            )
            patches.enter_context(
                patch.object(backup, "verify", side_effect=ValueError("restore failed"))
            )
            client = Mock()
            request = {
                "version": "1.2.3",
                "revision": "a" * 40,
                "image": "ghcr.io/papyrusreader/server@sha256:" + "b" * 64,
            }
            with self.assertRaisesRegex(ValueError, "restore failed"):
                backup.backup(client, "bucket", work, request)
            client.upload_file.assert_called_once()
            self.assertEqual(json.loads(previous.read_text()), prior)

    def test_unrelated_object_key_rejected_before_download(self):
        client = Mock()
        with self.assertRaisesRegex(ValueError, "object key"):
            backup.verify(client, "bucket", "backups/latest.tar.age", None)
        client.download_file.assert_not_called()
