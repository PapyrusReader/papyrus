#!/usr/bin/env python3
"""Install the Papyrus deployment process into its existing Octopus project."""
import argparse
import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SERVER = "https://papyrus-reader.octopus.app"
STEPS = (
    ("Verify the merged workspace release manifest", "prepare", False),
    ("Build the server image", "server-build", False),
    ("Build and sign client artifacts", "client-build", True),
    ("Verify the off-host backup and deploy the server digest", "server-deploy", False),
    ("Publish signed downloads", "publish", False),
    ("Deliver and verify the web app", "web", False),
    ("Deliver and verify Google Play internal", "play", True),
)


def api(key, path, document=None):
    headers = {"X-Octopus-ApiKey": key, "Content-Type": "application/json"}
    data = json.dumps(document).encode() if document is not None else None
    req = Request(SERVER + path, data=data, headers=headers, method="PUT" if data else "GET")
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        message = json.load(error)
        raise RuntimeError(f"{error.code} {path}: {message.get('ErrorMessage')} {message.get('Errors', [])}") from None


def make_step(title, stage, parallel, worker_pool):
    script = Path(__file__).with_name("worker.sh").read_text().replace("STAGE_NAME", stage)
    properties = {
        "Octopus.Action.RunOnServer": "true", "Octopus.Action.Script.ScriptSource": "Inline",
        "Octopus.Action.Script.Syntax": "Bash", "Octopus.Action.Script.ScriptBody": script,
    }
    if stage in ("server-build", "client-build"):
        properties["Octopus.Action.AutoRetry.MaximumCount"] = "3"
        properties["Octopus.Action.AutoRetry.MinimumBackoff"] = "15"
    return {
        "Name": title, "PackageRequirement": "LetOctopusDecide", "Condition": "Success",
        "StartTrigger": "StartWithPrevious" if parallel else "StartAfterPrevious",
        "Properties": {}, "Actions": [{
            "Name": title, "ActionType": "Octopus.Script", "IsRequired": True, "IsDisabled": False,
            "WorkerPoolId": worker_pool, "Properties": properties,
        }],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--dispatch-key-file", type=Path, required=True)
    parser.add_argument("--known-hosts-file", type=Path, required=True)
    args = parser.parse_args()
    key = args.api_key_file.read_text().strip()
    space = next(s["Id"] for s in api(key, "/api/spaces/all") if s["Name"] == "Default")
    root = f"/api/{space}"
    project = next(p for p in api(key, root + "/projects/all") if p["Slug"] == "papyrus")
    pool = next(p["Id"] for p in api(key, root + "/workerpools/all") if p["Name"] == "Hosted Ubuntu")
    process_path = project["Links"]["DeploymentProcess"]
    process = api(key, process_path)
    existing = {step["Name"]: step for step in process["Steps"]}
    old_prepare = "Approve the workspace release manifest"
    if old_prepare in existing:
        existing[STEPS[0][0]] = existing.pop(old_prepare)
    previous_title = "Verify the Mac backup and deploy the server digest"
    backup_title = next(title for title, stage, _ in STEPS if stage == "server-deploy")
    if previous_title in existing and backup_title not in existing:
        existing[backup_title] = existing.pop(previous_title)
    if set(existing) - {title for title, _, _ in STEPS}:
        raise ValueError("Existing process has unrelated steps")
    known_host = args.known_hosts_file.read_text().strip()
    if len(known_host.splitlines()) != 1 or len(known_host.split()) != 3:
        raise ValueError("Expected one SSH known_hosts entry")
    variables_path = project["Links"]["Variables"]
    variables = api(key, variables_path)
    for name, value, sensitive in (
        ("Papyrus.Host", known_host.split()[0], False),
        ("Papyrus.HostKey", known_host, False),
        ("Papyrus.DispatchKey", base64.b64encode(args.dispatch_key_file.read_bytes()).decode(), True),
    ):
        matches = [v for v in variables["Variables"] if v["Name"] == name]
        if len(matches) > 1 or (matches and matches[0].get("Scope")):
            raise ValueError(f"Unexpected scope or duplicate variable: {name}")
        variable = matches[0] if matches else {"Name": name, "Scope": {}}
        variable.update(Value=value, Type="Sensitive" if sensitive else "String", IsSensitive=sensitive)
        if not matches:
            variables["Variables"].append(variable)
    api(key, variables_path, variables)
    steps = []
    for title, stage, parallel in STEPS:
        step = make_step(title, stage, parallel, pool)
        if title in existing:
            step["Id"] = existing[title]["Id"]
            step["Actions"][0]["Id"] = existing[title]["Actions"][0]["Id"]
        steps.append(step)
    process["Steps"] = steps
    saved = api(key, process_path, process)
    print(f"Saved {len(saved['Steps'])} steps to {project['Name']}, process version {saved['Version']}")


if __name__ == "__main__":
    main()
