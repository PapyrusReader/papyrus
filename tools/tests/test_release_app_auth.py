import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "octopus"))
import pipeline  # add the workspace release modules to the standalone worker import path
from app_auth import AppGitHub


class ReleaseAppTests(TestCase):
    def test_installation_identity_permissions_and_refresh(self):
        for review in (True, False):
            slug = "papyrus-release-reviewer" if review else "papyrus-release-runner"
            with self.subTest(review=review), patch.dict(os.environ, {
                "PAPYRUS_APP_ID": "123", "PAPYRUS_APP_PRIVATE_KEY": "test", "GITHUB_ACTIONS": "false",
            }), patch("app_auth.time.time", return_value=1000) as clock, patch(
                "app_auth.subprocess.check_output", return_value=b"signature"
            ), patch("app_auth.api") as api:
                api.side_effect = [
                    {"slug": slug}, {"id": 42}, {"expires_at": "1970-01-01T01:17:00Z", "token": "first"},
                    {"slug": slug}, {"id": 42}, {"expires_at": "1970-01-01T02:17:00Z", "token": "second"},
                ]
                hub = AppGitHub(Path.cwd(), review=review)
                self.assertNotIn("PAPYRUS_APP_PRIVATE_KEY", os.environ)
                hub.refresh()
                hub.refresh()
                self.assertEqual(api.call_count, 3)
                endpoint, _, body = api.call_args.args
                self.assertEqual(endpoint, "/app/installations/42/access_tokens")
                self.assertEqual(body["repositories"], ["papyrus", "client", "server"])
                if review:
                    self.assertEqual(body["permissions"], {"contents": "read", "pull_requests": "write"})
                self.assertEqual(hub.login, slug + "[bot]")
                clock.return_value = 4500
                hub.refresh()
                self.assertEqual(os.environ["GH_TOKEN"], "second")
                api.side_effect = [{"slug": "unexpected"}]
                clock.return_value = 9000
                with self.assertRaisesRegex(ValueError, "Expected"):
                    hub.refresh()
