"""A published artifact or a saved note cannot prove the running deployment."""

import copy
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "release_deployment", Path(__file__).resolve().parents[1] / "release_deployment.py"
)
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)

TARGET = {"url": "https://api.example.test", "ssh_host": "production", "container": "papyrus-api-1"}
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE_RECORD = f"ghcr.io/papyrusreader/server:0.1.0\nDigest {DIGEST}\n"
RUNNING = {
    "labels": {"org.opencontainers.image.revision": REVISION},
    "digests": [f"ghcr.io/papyrusreader/server@{DIGEST}"],
    "version": "0.1.0",
    "migrations": ["new_migration"],
}


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        http = patch.object(deployment, "request_json", side_effect=[
            {"status": "healthy"}, {"info": {"version": "0.1.0"}},
        ])
        self.http = http.start()
        self.addCleanup(http.stop)
        host = patch.object(deployment, "inspect_server", return_value=copy.deepcopy(RUNNING))
        self.host = host.start()
        self.addCleanup(host.stop)

    def verify(self, **overrides):
        arguments = dict(version="0.1.0", revision=REVISION, image_record=IMAGE_RECORD, migrations=["new_migration"])
        return deployment.verify_server(TARGET, **(arguments | overrides))

    def test_requires_healthy_api_exact_release_digest_and_migration(self):
        result = self.verify()
        self.assertEqual(result, {"revision": REVISION, "digest": DIGEST, "migrations": ["new_migration"]})

    def test_old_healthy_api_is_not_a_completed_release(self):
        self.http.side_effect = [{"status": "healthy"}, {"info": {"version": "0.0.3"}}]

        with self.assertRaisesRegex(deployment.DeploymentError, "live API is 0.0.3, expected 0.1.0"):
            self.verify()

        self.host.assert_not_called()

    def test_unhealthy_api_is_rejected(self):
        self.http.side_effect = [{"status": "unhealthy"}]

        with self.assertRaisesRegex(deployment.DeploymentError, "not healthy"):
            self.verify()

    def test_malformed_api_metadata_is_rejected(self):
        self.http.side_effect = [{"status": "healthy"}, {"info": None}]

        with self.assertRaisesRegex(deployment.DeploymentError, "invalid version metadata"):
            self.verify()

    def test_api_unavailable_never_reuses_previous_success(self):
        self.http.side_effect = deployment.DeploymentError("Cannot verify public API")

        with self.assertRaises(deployment.DeploymentError):
            self.verify()

    def test_wrong_image_package_or_migration_is_rejected(self):
        for field, value, error in [
            ("labels", {"org.opencontainers.image.revision": "c" * 40}, "release commit"),
            ("digests", ["ghcr.io/papyrusreader/server@sha256:wrong"], "published digest"),
            ("version", "0.0.3", "release version"),
            ("migrations", ["old_migration"], "Database migration"),
        ]:
            with self.subTest(field=field):
                self.http.side_effect = [{"status": "healthy"}, {"info": {"version": "0.1.0"}}]
                self.host.return_value = copy.deepcopy(RUNNING) | {field: value}

                with self.assertRaisesRegex(deployment.DeploymentError, error):
                    self.verify()

    def test_missing_digest_cannot_be_attested(self):
        with self.assertRaisesRegex(deployment.DeploymentError, "digest is missing"):
            self.verify(image_record="what")

    def test_target_rejects_credentials_and_shell_or_ssh_options(self):
        with self.assertRaises(deployment.DeploymentError):
            deployment.validate_target({"url": None})

        for field, value in [
            ("url", "https://user:secret@api.example.test"),
            ("url", "http://api.example.test"),
            ("url", "https://api.example.test/v1"),
            ("ssh_host", "-oProxyCommand=anything"),
            ("ssh_host", "host; touch unwanted"),
            ("container", "api; touch unwanted"),
        ]:
            with self.subTest(field=field), self.assertRaises(deployment.DeploymentError):
                deployment.validate_target(TARGET | {field: value})


class ProbeTest(unittest.TestCase):
    def test_migration_heads_are_read_without_executing_release_code(self):
        sources = [
            "raise RuntimeError('never execute')\nrevision: str = 'one'\ndown_revision = None",
            "revision = 'two'\ndown_revision = 'one'",
            "revision = 'three'\ndown_revision = 'one'",
            "revision = 'four'\ndown_revision = ('two', 'three')",
        ]
        self.assertEqual(deployment.migration_heads(sources), ["four"])

        with self.assertRaises(deployment.DeploymentError):
            deployment.migration_heads([])

    def test_remote_commands_are_quoted_and_time_bounded(self):
        with patch.object(deployment.subprocess, "run", return_value=Mock(returncode=0, stdout="ok")) as run:
            self.assertEqual(deployment.remote(TARGET, ["python", "-c", "print('safe')"]), "ok")

        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-2], "production")
        self.assertIn("BatchMode=yes", arguments)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertEqual(deployment.shlex.split(arguments[-1]), ["python", "-c", "print('safe')"])

    def test_remote_failures_do_not_leak_process_output(self):
        with patch.object(deployment.subprocess, "run", return_value=Mock(returncode=1, stderr="secret")):
            with self.assertRaises(deployment.DeploymentError) as caught:
                deployment.remote(TARGET, ["docker", "inspect"])

        self.assertNotIn("secret", str(caught.exception))

        with patch.object(deployment.subprocess, "run", side_effect=subprocess.TimeoutExpired("ssh", 30)):
            with self.assertRaises(deployment.DeploymentError):
                deployment.remote(TARGET, ["docker", "inspect"])

    def test_image_switch_during_inspection_is_rejected(self):
        responses = [
            json.dumps({"Running": True, "Health": {"Status": "healthy"}}),
            "sha256:original", json.dumps(RUNNING), json.dumps(RUNNING), "sha256:changed",
        ]

        with patch.object(deployment, "remote", side_effect=responses):
            with self.assertRaisesRegex(deployment.DeploymentError, "changed during verification"):
                deployment.inspect_server(TARGET)

    def test_unhealthy_container_is_rejected(self):
        with patch.object(deployment, "remote", return_value=json.dumps({"Running": False})):
            with self.assertRaisesRegex(deployment.DeploymentError, "not healthy"):
                deployment.inspect_server(TARGET)


if __name__ == "__main__":
    unittest.main()
