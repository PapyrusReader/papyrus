#!/usr/bin/env python3
"""Validate Compose routing, public ports and private database boundaries."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compose(source: Path, sample: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        shutil.copyfile(source, target / "compose.yml")
        values = {}
        if sample:
            for line in sample.read_text().splitlines():
                if line.strip() and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    values[key] = value
        for key in ("POSTGRES_PASSWORD", "POWERSYNC_SOURCE_PASSWORD", "POWERSYNC_STORAGE_PASSWORD", "SECRET_KEY"):
            if key in values:
                values[key] = "a" * 64
        if "ACME_EMAIL" in values:
            values["ACME_EMAIL"] = "deployment@example.org"
        environment = target / "production.env"
        environment.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        output = subprocess.check_output([
            "docker", "compose", "--env-file", str(environment), "-f",
            str(target / "compose.yml"), "config", "--format", "json",
        ], text=True)
        return json.loads(output)


def main() -> None:
    edge = compose(ROOT / "deploy/edge/compose.yml", ROOT / "deploy/edge/edge.env.example")
    app = compose(ROOT / "server/deploy/compose.yml", ROOT / "server/deploy/production.env.example")
    website = compose(ROOT / "website/deploy/compose.yml")
    assert {edge["name"], app["name"], website["name"]} == {
        "papyrus-edge", "papyrus-production", "papyrus-website",
    }, "Each deployment must have its own Compose project"
    for project in (edge, app, website):
        assert project["networks"]["edge"]["external"]
        assert project["networks"]["edge"]["name"] == "papyrus-edge"
    assert set(edge["services"]) == {"proxy"}
    proxy = edge["services"]["proxy"]
    assert {int(port["published"]) for port in proxy["ports"]} == {80, 443}
    assert {volume["target"] for volume in proxy["volumes"]} == {
        "/etc/caddy/Caddyfile", "/data", "/config",
    }, "The edge proxy must not mount app or website content"
    for project in (app, website):
        for service in project["services"].values():
            assert not service.get("ports"), "Only the edge project may publish ports"
    for database in ("database", "powersync-storage"):
        assert set(app["services"][database]["networks"]) == {"default"}
    for name, alias in (("api", "papyrus-api"), ("powersync", "papyrus-sync"), ("web", "papyrus-app")):
        assert alias in app["services"][name]["networks"]["edge"]["aliases"]
    assert any(volume["target"] == "/srv/web" and volume["read_only"] for volume in app["services"]["web"]["volumes"]), (
        "Mount the web parent directory so atomic current symlink replacements are visible"
    )
    assert set(website["services"]) == {"website"}
    site = website["services"]["website"]
    assert set(site["networks"]) == {"edge"}
    assert "papyrus-website" in site["networks"]["edge"]["aliases"]
    assert len(site["volumes"]) == 1
    assert site["volumes"][0]["target"] == "/srv/site"
    assert site["volumes"][0]["read_only"]
    print("Three independent Compose projects validated; only the edge publishes ports and databases stay private")


if __name__ == "__main__":
    main()
