import io
import json
import subprocess
import sys
import tempfile
import unittest
from email.headerregistry import Address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_notifications as alerts  # noqa: E402
import release_notify_smtp as smtp  # noqa: E402
from release_manager import step  # noqa: E402
from release_runner import Decision, Runner  # noqa: E402


RELEASE = "papyrus-1.0.0+5"
LINK = "https://github.com/PapyrusReader/server/actions/runs/123"


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = Path(directory.name)

    def test_delivery_is_deduplicated_across_restarts(self):
        with patch.object(alerts.subprocess, "run") as command:
            for _ in range(2):
                alerts.ReleaseNotifications(self.storage, "/send").send(RELEASE, "waiting", "Deploy server")

            command.assert_called_once()
            args, kwargs = command.call_args
            self.assertEqual(args, (["/send"],))
            self.assertEqual(json.loads(kwargs["input"]), {
                "release": RELEASE, "state": "waiting", "detail": "Deploy server",
            })

    def test_failed_delivery_retries_without_recording_success(self):
        notifier = alerts.ReleaseNotifications(self.storage, "/send")

        with (
            patch.object(alerts.subprocess, "run", side_effect=[subprocess.CalledProcessError(1, "/send"), None]) as command,
            patch.object(alerts.time, "monotonic", side_effect=[100, 100, 110, 161]),
            patch.object(alerts.sys, "stderr", io.StringIO()) as stderr,
        ):
            for _ in range(3):
                notifier.send(RELEASE, "waiting", "Deploy server")

            self.assertEqual(command.call_count, 2)
            self.assertIn("notification delivery or recording failed", stderr.getvalue())

        self.assertEqual(len(list((self.storage / "notifications").glob("*.json"))), 1)

    def test_terminal_notice_retries_before_returning(self):
        notifier = alerts.ReleaseNotifications(self.storage, "/send")

        with (
            patch.object(alerts.subprocess, "run", side_effect=[subprocess.TimeoutExpired("/send", 20), None]) as command,
            patch.object(alerts.time, "sleep") as sleep,
        ):
            notifier.send(RELEASE, "blocked", "Release workflow failed")

        self.assertEqual(command.call_count, 2)
        sleep.assert_called_once_with(1)


class AttentionTest(unittest.TestCase):
    def setUp(self):
        self.notify = Mock()
        self.options = SimpleNamespace(release=RELEASE, merge=True, deploy_server=None, repair=False, dry_run=False)
        self.runner = Runner(None, self.options, None, notify=self.notify)

    def test_manual_deployment_and_review_are_actionable_waits(self):
        run = {"headSha": "release-sha", "status": "completed", "conclusion": "success", "url": LINK}
        decision = self.runner.decide({"version": "1.0.0"}, {
            "server": step("server", "published", "live API is 0.9.0", deployment_configured=True,
                           revision="release-sha", live_version="0.9.0", run=run),
            "client": step("client", "prepare", "review required (PR 42)", gate="review_pending"),
        })
        self.assertEqual(decision.state, "waiting")
        self.runner.notify_decision(decision)
        self.assertIn(LINK, self.notify.call_args_list[0].args[2])
        self.assertEqual(self.notify.call_args_list[1].args, (RELEASE, "waiting", "client: review required (PR 42)"))

    def test_pending_ci_and_dry_runs_are_quiet(self):
        decision = self.runner.decide({"version": "1.0.0"}, {
            "client": step("client", "prepare", "CI is running", gate="ci_pending"),
        })
        self.runner.notify_decision(decision)
        self.options.dry_run = True
        self.runner.notify_decision(Decision("blocked", "A test failed"))
        self.notify.assert_not_called()

    def test_blocked_and_timed_out_releases_send_an_alert(self):
        self.runner.notify_decision(Decision("blocked", "A test failed"))
        self.runner.notify_decision(Decision("timeout", "Release wait timeout expired"))
        self.assertEqual([call.args for call in self.notify.call_args_list], [
            (RELEASE, "blocked", "A test failed"),
            (RELEASE, "timeout", "Release wait timeout expired"),
        ])


class MailTest(unittest.TestCase):
    def setUp(self):
        self.settings = smtp.MailSettings(
            "smtp.example.test", 587, "user", "example", Address(addr_spec="sender@example.test"),
            Address(addr_spec="ops@example.test"), "starttls",
        )

    def test_message_uses_the_configured_recipient_and_action(self):
        message = smtp.message_for(self.settings, {
            "release": RELEASE, "state": "waiting", "detail": f"Deploy server. {LINK}", "to": "elsewhere@example.test",
        })
        self.assertEqual(message["To"], "ops@example.test")
        self.assertIn(RELEASE, message["Subject"])
        self.assertIn(LINK, message.get_content())

    def test_starttls_and_login_run_before_delivery(self):
        message = smtp.message_for(self.settings, {"release": RELEASE, "state": "blocked", "detail": "Fix test"})

        with patch.object(smtp.smtplib, "SMTP") as factory:
            smtp.deliver(self.settings, message)

        connection = factory.return_value
        self.assertEqual([call[0] for call in connection.method_calls], ["starttls", "login", "send_message"])
        connection.send_message.assert_called_once_with(message)

    def test_smtp_check_does_not_send_email(self):
        with patch.object(smtp.smtplib, "SMTP") as factory:
            smtp.deliver(self.settings, None)

        factory.return_value.login.assert_called_once()
        factory.return_value.send_message.assert_not_called()
