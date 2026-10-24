"""Read-only release discovery and concise status summaries."""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone

from release_manager import ReleaseError, step

READ_ERRORS = (ReleaseError, OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired)
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def matches(record, selector):
    return selector in (record["id"], record["version"], record["id"].removeprefix("papyrus-"))


def summary(observations):
    stages = {item["stage"] for item in observations.values()}

    for stage in ("unknown", "blocked"):
        if stage in stages:
            return stage

    if any(
        item["stage"] in ("publishing", "published")
        and (item.get("run") or {}).get("conclusion") in ("failure", "cancelled", "timed_out")
        for item in observations.values()
    ):
        return "failed"

    if "missing" in stages:
        return "incomplete"

    if "draft" in stages:
        return "draft"

    if stages == {"complete"}:
        return "complete"

    if stages and stages <= {"verified", "complete"}:
        return "verified"

    if stages and stages <= {"published", "verified", "complete"}:
        return "published"

    for stage in ("sync", "publishing", "promote", "prepared", "prepare", "planned", "waiting"):
        if stage in stages:
            return stage

    return "unknown"


class Catalog:
    def __init__(self, manager):
        self.manager = manager
        self.errors = []
        self.publications = {}
        self.observations = {}
        self.unavailable = set()

    def saved(self):
        records = {}

        for directory, source in ((self.manager.root / "releases", "archive"), (self.manager.storage, "local")):
            for path in directory.glob("*.json"):
                plan = json.loads(path.read_text())
                records[plan["id"]] = {
                    **plan,
                    "plan": plan,
                    "source": source,
                    "updated": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                }

        return records

    def discover(self, component="all"):
        records = {
            release_id: record
            for release_id, record in self.saved().items()
            if component == "all" or record["id"].startswith(f"{component}-")
        }
        names = (
            ("server", "client", "website")
            if component == "all"
            else (("server", "client") if component == "papyrus" else ("website",))
        )

        for name in names:
            try:
                repo = self.manager.repo(name)
                publications = self.manager.github.releases(repo)
            except READ_ERRORS as error:
                self.unavailable.add(name)
                self.errors.append(f"{name}: {error}")
                continue

            pattern = rf"v({VERSION})" + (r"\+([1-9][0-9]*)" if name == "client" else "")

            for publication in publications:
                match = re.fullmatch(pattern, publication["tag_name"])

                if not match:
                    continue

                version = match[1]
                build = int(match[2]) if name == "client" else None
                component_record = {"repo": repo, "tag": publication["tag_name"], "publication": publication}
                self.publications[(name, version, build)] = component_record

                if name == "server":
                    continue

                release_id = f"papyrus-{version}+{build}" if name == "client" else f"website-{version}"

                if release_id not in records:
                    records[release_id] = {
                        "id": release_id,
                        "version": version,
                        "build": build,
                        "source": "github",
                        "updated": publication.get("published_at") or publication.get("created_at") or "",
                        "components": {name: component_record},
                    }

        represented = {record["version"] for record in records.values() if record["id"].startswith("papyrus-")}

        for (name, version, _), component_record in self.publications.items():
            if name != "server" or version in represented:
                continue

            publication = component_record["publication"]
            release_id = f"server-{version}"
            records[release_id] = {
                "id": release_id,
                "version": version,
                "build": None,
                "source": "github",
                "updated": publication.get("published_at") or publication.get("created_at") or "",
                "components": {"server": component_record},
            }

        for record in records.values():
            if record["source"] != "github" or not record["id"].startswith("papyrus-"):
                continue

            server = self.publications.get(("server", record["version"], None))
            record["components"] = {"server": server, **record["components"]}

        return records

    def published_status(self, record, name, component):
        if component is None:
            stage = "unknown" if name in self.unavailable else "missing"
            return step(name, stage, "publication unavailable" if stage == "unknown" else "no matching publication")

        publication = component["publication"]

        if publication["draft"]:
            return step(name, "draft", publication["html_url"])

        key = (component["repo"], component["tag"])

        if key not in self.observations:
            self.manager.fetch(name)
            revision = self.manager.revision(name, component["tag"])

            if not revision:
                observation = step(name, "unknown", "published tag is unavailable")
            elif not self.manager.matches(record, name, revision):
                observation = step(name, "blocked", "tag version mismatch")
            elif not self.manager.ancestor(name, revision, "origin/master"):
                observation = step(name, "blocked", "tag is not on master")
            else:
                observation = self.manager.publication(record, name, revision)

            self.observations[key] = {
                **observation,
                "url": publication["html_url"],
                "prerelease": publication.get("prerelease", False),
            }

        return self.observations[key]

    def status(self, record):
        if "plan" in record:
            return self.manager.status(record["plan"])

        observations = {}

        for name, component in record["components"].items():
            try:
                observations[name] = self.published_status(record, name, component)
            except READ_ERRORS as error:
                self.errors.append(f"{record['id']} {name}: {error}")
                observations[name] = step(name, "unknown", str(error))

        return observations

    def list(self, component="all", limit=20):
        records = self.discover(component)
        selected = sorted(records.values(), key=lambda record: (record["updated"], record["id"]), reverse=True)[:limit]
        rows = []

        for record in selected:
            try:
                observations = self.status(record)
            except READ_ERRORS as error:
                self.errors.append(f"{record['id']}: {error}")
                observations = {name: step(name, "unknown", str(error)) for name in record["components"]}

            rows.append(
                {
                    "release": record["id"],
                    "status": summary(observations),
                    "source": record["source"],
                    "components": list(observations.values()),
                }
            )

        return rows

    def inspect(self, selector):
        if not re.fullmatch(rf"(?:(?:papyrus|website|server)-)?{VERSION}(?:\+[1-9][0-9]*)?", selector):
            raise ReleaseError("Use a version or release ID")

        candidates = [record for record in self.saved().values() if matches(record, selector)]

        exact = len(candidates) == 1 and selector in (
            candidates[0]["id"],
            candidates[0]["id"].removeprefix("papyrus-") if "+" in selector else "",
        )

        if len(candidates) <= 1 and not exact:
            component = "all"

            if selector.startswith("website-"):
                component = "website"
            elif selector.startswith(("papyrus-", "server-")) or "+" in selector:
                component = "papyrus"

            candidates = [record for record in self.discover(component).values() if matches(record, selector)]

        if len(candidates) != 1:
            if self.errors:
                raise ReleaseError("Discovery incomplete: " + "; ".join(self.errors))

            raise ReleaseError("Release not found or ambiguous. Use the full ID from release list.")

        record = candidates[0]
        observations = self.status(record)

        if self.errors:
            raise ReleaseError("Status incomplete: " + "; ".join(self.errors))

        return record, observations


def render_list(rows, errors, as_json=False):
    if as_json:
        print(json.dumps({"releases": rows, "errors": errors}, indent=2))
        return

    if rows:
        width = max(len(row["release"]) for row in rows)
        print(f"{'RELEASE':<{width}}  {'STATUS':<11} COMPONENTS")

        for row in rows:
            components = " ".join(f"{item['component']}:{item['stage']}" for item in row["components"])
            print(f"{row['release']:<{width}}  {row['status']:<11} {components}")
    elif not errors:
        print("No releases.")

    for error in errors:
        print(f"release: {error}", file=sys.stderr)
