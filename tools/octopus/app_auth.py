"""Short-lived credentials for the two release GitHub Apps on a hosted runner."""
import base64
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from release_manager import GitHub


def encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def api(path, token, body=None):
    request = Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "Content-Type": "application/json", "User-Agent": "Papyrus-release",
    }, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"GitHub App request {path} returned HTTP {error.code}") from None


class AppGitHub(GitHub):
    def __init__(self, root, *, review):
        super().__init__(root)
        self.app_id = os.environ["PAPYRUS_APP_ID"]
        self.key = os.environ.pop("PAPYRUS_APP_PRIVATE_KEY").encode()
        self.slug = "papyrus-release-reviewer" if review else "papyrus-release-runner"
        self.login = self.slug + "[bot]"
        self.permissions = {"contents": "read", "pull_requests": "write"}
        if not review:
            self.permissions.update(contents="write", actions="write", checks="read", statuses="read")
        self.expires = 0

    def refresh(self):
        now = int(time.time())
        if now + 300 < self.expires:
            return
        payload = json.dumps({"iss": self.app_id, "iat": now - 60, "exp": now + 540}).encode()
        unsigned = encode(b'{"alg":"RS256","typ":"JWT"}') + b"." + encode(payload)
        with tempfile.TemporaryFile() as key:
            key.write(self.key)
            key.seek(0)
            signature = subprocess.check_output(
                ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{key.fileno()}"],
                input=unsigned, pass_fds=(key.fileno(),),
            )
        jwt = (unsigned + b"." + encode(signature)).decode()
        if api("/app", jwt)["slug"] != self.slug:
            raise ValueError(f"Expected the {self.slug} GitHub App")
        installation = api("/orgs/PapyrusReader/installation", jwt)
        issued = api(f"/app/installations/{installation['id']}/access_tokens", jwt, {
            "repositories": ["papyrus", "client", "server"], "permissions": self.permissions,
        })
        self.expires = datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00")).timestamp()
        os.environ["GH_TOKEN"] = issued["token"]
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::add-mask::" + issued["token"], flush=True)

    def call(self, *args):
        self.refresh()
        return super().call(*args)

    def api(self, endpoint, *, missing=False):
        self.refresh()
        return super().api(endpoint, missing=missing)
