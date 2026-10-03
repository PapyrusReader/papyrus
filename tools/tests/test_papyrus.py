"""Exercise command dispatch, check outcomes, and destructive test-db boundaries."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader(
    "papyrus_cli", str(Path(__file__).resolve().parents[1] / "papyrus")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


class WorkspaceCommandsTest(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.errors = contextlib.redirect_stderr(io.StringIO())
        self.output.__enter__()
        self.errors.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.addCleanup(self.errors.__exit__, None, None, None)

    def test_test_selector_and_options_reach_correct_component(self):
        with patch.object(cli, "test", return_value=3) as test:
            result = cli.main(
                [
                    "test",
                    "client",
                    "--",
                    "test/auth/token_store_test.dart",
                    "--plain-name",
                    "refresh token",
                ]
            )
        self.assertEqual(result, 3)
        test.assert_called_once_with(
            "client",
            ["test/auth/token_store_test.dart", "--plain-name", "refresh token"],
        )

    def test_commands_preserve_arguments_without_shell_interpretation(self):
        args = ["node", "a file.js", "$(touch unexpected)"]
        with patch.object(
            cli.subprocess, "run", return_value=subprocess.CompletedProcess(args, 0)
        ) as run:
            self.assertEqual(cli.run(args, cli.CLIENT), 0)
        run.assert_called_once_with(args, cwd=cli.CLIENT, env=None, check=False)

    def test_missing_command_returns_failure(self):
        with patch.object(
            cli.subprocess,
            "run",
            side_effect=FileNotFoundError(2, "not found", "missing"),
        ):
            self.assertEqual(cli.run(["missing"]), 127)

    def test_checks_continue_and_propagate_any_failure(self):
        commands = [(["first"], cli.ROOT), (["second"], cli.ROOT)]
        with (
            patch.object(cli, "checks", return_value=commands),
            patch.object(cli, "run", side_effect=[1, 0]) as run,
        ):
            self.assertEqual(cli.check("server"), 1)
        self.assertEqual(run.call_count, 2)

    def test_client_format_check_does_not_write_files(self):
        with patch.object(cli, "sdk_command", side_effect=lambda name: name):
            args, cwd = cli.checks("client")[0]
        self.assertIn("--output=none", args)
        self.assertIn("--set-exit-if-changed", args)
        self.assertEqual(cwd, cli.CLIENT)

    def test_all_checks_include_reader_and_reference_gate(self):
        with patch.object(cli, "checks", return_value=[]) as checks:
            self.assertEqual(cli.check("all"), 0)
        self.assertEqual(
            [call.args[0] for call in checks.call_args_list],
            ["tooling", "references", "client", "reader", "server"],
        )

    def test_all_flutter_dependencies_enforce_locks(self):
        with (
            patch.object(cli, "sdk_command", return_value="flutter"),
            patch.object(cli, "run", return_value=0) as run,
        ):
            self.assertEqual(cli.main(["deps", "all"]), 0)
        self.assertEqual(
            [call.args[1] for call in run.call_args_list],
            [cli.CLIENT, cli.READER, cli.SERVER],
        )
        for call in run.call_args_list[:2]:
            self.assertIn("--enforce-lockfile", call.args[0])

    def test_sdk_absent_does_not_fall_back_to_global_flutter(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(cli, "ROOT", Path(directory)),
        ):
            with self.assertRaisesRegex(RuntimeError, "SDK is missing"):
                cli.sdk_command("flutter")

    def test_server_rejects_application_remote_or_unmarked_database(self):
        for host, database in [
            ("127.0.0.1", "papyrus"),
            ("remote.example.com", "papyrus_test"),
            ("127.0.0.1", "library"),
        ]:
            with self.subTest(host=host, database=database):
                result = subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        {"host": host, "database": database, "app_database": "papyrus"}
                    ),
                )
                with (
                    patch.object(cli.subprocess, "run", return_value=result),
                    patch.object(cli, "run") as run,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "distinct local database"
                    ):
                        cli.test_server([])
                run.assert_not_called()

    def test_server_lock_blocks_overlap_and_is_released_after_failure(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "host": "localhost",
                    "database": "papyrus_test",
                    "app_database": "papyrus",
                }
            ),
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(cli, "ROOT", Path(directory)),
        ):
            lock = Path(directory) / ".local/server-test.lock"
            lock.mkdir(parents=True)
            with (
                patch.object(cli.subprocess, "run", return_value=result),
                patch.object(cli, "run") as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "Another server test"):
                    cli.test_server([])
                run.assert_not_called()
                lock.rmdir()
                run.return_value = 4
                self.assertEqual(cli.test_server(["tests/test_models.py"]), 4)
                self.assertFalse(lock.exists())
                self.assertIn("--locked", run.call_args.args[0])
                self.assertIn("tests/test_models.py", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
