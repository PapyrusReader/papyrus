"""Transient terminal feedback for release operations, without affecting their output."""

import contextlib
import contextvars
import itertools
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ACTIVE = contextvars.ContextVar("release_progress", default=None)


def command_label(args, cwd):
    if args[0] == "git":
        operation = args[3] if len(args) > 3 else ""
        labels = {
            "fetch": f"Fetching {Path(cwd).name} refs",
            "push": "Pushing release branch",
            "commit": "Committing release metadata",
            "worktree": "Preparing release worktree",
        }
        return labels.get(operation, "Checking Git history")

    repo = args[args.index("--repo") + 1].split("/")[-1] if "--repo" in args else "release"
    labels = {
        ("pr", "list"): f"Checking {repo} PRs",
        ("pr", "view"): f"Checking {repo} PR",
        ("pr", "create"): f"Creating {repo} PR",
        ("pr", "merge"): f"Merging {repo} PR",
        ("run", "list"): f"Checking {repo} workflows",
        ("run", "view"): f"Checking {repo} delivery",
        ("run", "rerun"): f"Retrying {repo} workflow",
        ("workflow", "run"): f"Starting {repo} workflow",
    }

    if len(args) > 2 and args[1] == "api":
        if args[2] == "graphql":
            return "Checking PR reviews"

        parts = args[2].split("/")

        if len(parts) > 3 and parts[0] == "repos" and parts[3].startswith("releases"):
            return f"Checking {parts[2]} releases"

    return labels.get(tuple(args[1:3]), "Checking GitHub")


def update(label):
    spinner = ACTIVE.get()

    if spinner is not None:
        spinner.label = label


class Spinner:
    def __init__(self, label, enabled=True):
        self.stream = sys.stderr
        self.enabled = (
            enabled
            and sys.stdout.isatty()
            and self.stream.isatty()
            and os.environ.get("TERM") != "dumb"
            and not os.environ.get("CI")
        )
        self.label = label
        self.started = time.monotonic()
        self.stopped = threading.Event()
        self.thread = None
        self.visible = False
        self.frames = itertools.cycle("|/-\\")

    def draw(self):
        width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
        label = "".join(character if character.isprintable() else " " for character in self.label)
        line = f"{next(self.frames)} {label} ({int(time.monotonic() - self.started)}s)"

        try:
            self.stream.write("\r\033[2K" + line[:width])
            self.stream.flush()
            self.visible = True
        except (OSError, ValueError):
            self.stopped.set()

    def animate(self):
        while not self.stopped.wait(0.1):
            self.draw()

    def __enter__(self):
        if self.enabled:
            self.draw()
            self.thread = threading.Thread(target=self.animate, name="release-spinner", daemon=True)
            self.thread.start()

        return self

    def __exit__(self, *exception):
        self.stopped.set()

        if self.thread is not None:
            self.thread.join()

        if self.visible:
            with contextlib.suppress(OSError, ValueError):
                self.stream.write("\r\033[2K")
                self.stream.flush()


@contextlib.contextmanager
def activity(label, *, enabled=True):
    with Spinner(label, enabled) as spinner:
        token = ACTIVE.set(spinner)

        try:
            yield
        finally:
            ACTIVE.reset(token)
