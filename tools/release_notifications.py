"""Deliver release alerts through a configured command and remember successful deliveries."""

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


TERMINAL_STATES = {"blocked", "timeout", "error"}
PAYLOAD_LIMIT = 16384


class ReleaseNotifications:
    def __init__(self, storage, executable):
        self.storage = storage / "notifications"
        self.executable = executable
        self.retry_at = {}

    def send(self, release, state, detail):
        payload = {"release": release[:100], "state": state, "detail": detail[:2000]}
        message = json.dumps(payload, ensure_ascii=False).encode()
        fingerprint = hashlib.sha256(message).hexdigest()
        journal = self.storage / (hashlib.sha256(release.encode()).hexdigest() + ".json")

        if time.monotonic() < self.retry_at.get(fingerprint, 0):
            return

        try:
            sent = set(json.loads(journal.read_text())) if journal.exists() else set()

            if fingerprint in sent:
                return

            self.storage.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.deliver(message, terminal=state in TERMINAL_STATES)
            self.save(journal, sent | {fingerprint})
            self.retry_at.pop(fingerprint, None)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
            self.retry_at[fingerprint] = time.monotonic() + 60
            print(f"release: notification delivery or recording failed ({type(error).__name__})", file=sys.stderr)

    def deliver(self, message, *, terminal):
        for attempt in range(2 if terminal else 1):
            try:
                subprocess.run(
                    [self.executable], input=message, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=20, check=True,
                )
                return
            except (OSError, subprocess.SubprocessError):
                if not terminal or attempt:
                    raise

                time.sleep(1)

    def save(self, journal, fingerprints):
        with tempfile.NamedTemporaryFile("w", dir=self.storage, delete=False) as temporary:
            path = Path(temporary.name)

            try:
                json.dump(sorted(fingerprints), temporary)
                temporary.flush()
                path.replace(journal)
            finally:
                path.unlink(missing_ok=True)
