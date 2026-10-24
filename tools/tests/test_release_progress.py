"""Keep interactive progress from leaking into results or surviving interruption."""

import contextlib
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_manager  # noqa: E402
import release_progress as progress  # noqa: E402


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTest(unittest.TestCase):
    def test_spinner_updates_and_stops_on_interrupt(self):
        updated = threading.Event()

        class ObservedTerminal(Terminal):
            def write(self, text):
                result = super().write(text)

                if "Checking PR reviews" in text:
                    updated.set()

                return result

        output = Terminal()
        errors = ObservedTerminal()

        with (
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
            patch.dict("os.environ", {"TERM": "xterm"}, clear=True),
        ):
            with self.assertRaises(KeyboardInterrupt):
                with progress.activity("Fetching refs"):
                    spinner = progress.ACTIVE.get()
                    progress.update("Checking PR reviews")
                    self.assertTrue(updated.wait(timeout=2), "Spinner did not update while work was running")
                    raise KeyboardInterrupt

            self.assertIsNone(progress.ACTIVE.get())
            self.assertFalse(spinner.thread.is_alive())
            self.assertTrue(errors.getvalue().endswith("\r\033[2K"))
            self.assertEqual(output.getvalue(), "")

    def test_redirected_output_ci_and_dumb_terminals_are_silent(self):
        cases = (
            (io.StringIO(), Terminal(), {"TERM": "xterm"}),
            (Terminal(), io.StringIO(), {"TERM": "xterm"}),
            (Terminal(), Terminal(), {"TERM": "dumb"}),
            (Terminal(), Terminal(), {"TERM": "xterm", "CI": "true"}),
        )

        for output, errors, environment in cases:
            with (
                self.subTest(environment=environment),
                patch("sys.stdout", output),
                patch("sys.stderr", errors),
                patch.dict("os.environ", environment, clear=True),
            ):
                with progress.activity("Checking releases"):
                    spinner = progress.ACTIVE.get()
                    self.assertIsNone(spinner.thread)

                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "")

    def test_json_command_is_clean_even_when_attached_to_a_terminal(self):
        output = Terminal()
        errors = Terminal()
        manager = Mock()
        manager.lock = contextlib.nullcontext
        catalog = Mock()
        catalog.inspect.return_value = (
            {"id": "website-1.1.7"},
            {"website": release_manager.step("website", "verified")},
        )

        with (
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
            patch.dict("os.environ", {"TERM": "xterm"}, clear=True),
            patch.object(release_manager, "Manager", return_value=manager),
            patch("release_catalog.Catalog", return_value=catalog),
        ):
            result = release_manager.main(["status", "website-1.1.7", "--json"])

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["release"], "website-1.1.7")
        self.assertEqual(errors.getvalue(), "")

    def test_error_is_printed_after_progress_is_cleared(self):
        output = Terminal()
        errors = Terminal()
        manager = Mock()
        manager.lock = contextlib.nullcontext
        manager.advance.side_effect = release_manager.ReleaseError("Connection failed")

        with (
            patch("sys.stdout", output),
            patch("sys.stderr", errors),
            patch.dict("os.environ", {"TERM": "xterm"}, clear=True),
            patch.object(release_manager, "Manager", return_value=manager),
        ):
            result = release_manager.main(["advance", "website-1.1.7"])

        self.assertEqual(result, 1)
        self.assertTrue(errors.getvalue().endswith("\r\033[2Krelease: Connection failed\n"))
        self.assertIsNone(progress.ACTIVE.get())
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
