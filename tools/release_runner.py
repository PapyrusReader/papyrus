"""Drive a saved release from fresh observations, with durable handoffs."""

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

from release_deployment import DeploymentError
from release_manager import ReleaseChanged, ReleaseError
from release_notifications import TERMINAL_STATES, ReleaseNotifications


WAITING_GATES = {"ci_pending", "review_pending", "mergeability_pending", "review_threads"}
REPAIR_GATES = {"ci_failed", "conflicts"}
TRANSIENT_ERRORS = re.compile(
    r"timed? out|connection (?:reset|refused|interrupted)|network is unreachable|temporary failure|"
    r"could not resolve host|unexpected disconnect|remote end hung up|rate limit|(?:HTTP |code )(?:429|50[0234])\b",
    re.I,
)


@dataclass
class Decision:
    state: str
    detail: str
    job: tuple | None = None
    attention: tuple[str, ...] = ()


def deploy_server(target, executable, request):
    args = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "--", target["ssh_host"], shlex.join([executable]),
    ]

    try:
        result = subprocess.run(args, input=json.dumps(request), text=True, capture_output=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeploymentError("Server deployment job was interrupted; check its state on the host") from error

    if result.returncode:
        raise DeploymentError(f"Server deployment job exited {result.returncode}; check its host log")

    return "Server deployment job completed; checking the live release"


class Runner:
    def __init__(self, manager, options, emit, *, deploy=deploy_server, notify=None):
        self.manager = manager
        self.options = options
        self.emit = emit
        self.deploy = deploy
        self.notify = notify
        self.release_id = options.release

    def claim(self, plan, key, description, job):
        attempts = plan.setdefault("automation", {})
        attempt = attempts.get(key)

        if attempt:
            if attempt["state"] == "finished":
                return Decision("waiting", attempt["detail"])

            return Decision("blocked", attempt.get("detail") or f"{description} was interrupted; inspect before resuming")

        attempts[key] = {"state": "started"}
        self.manager.save(plan)
        return Decision("working", description, (key, job))

    def server_decision(self, plan, item):
        if not item.get("deployment_configured"):
            return Decision("blocked", f"server: {item['detail']}")

        current = item.get("live_version")
        executable = self.options.deploy_server
        run = item.get("run") or {}

        if isinstance(current, str) and re.fullmatch(r"\d+\.\d+\.\d+", current):
            live = tuple(map(int, current.split(".")))
            expected = tuple(map(int, plan["version"].split(".")))

            if live > expected:
                return Decision("blocked", f"server: {item['detail']}")

            if live < expected and executable:
                if run.get("status") != "completed":
                    return Decision("waiting", "server: waiting for the publication workflow to finish")

                request = self.manager.server_deployment_request(plan, item)
                target = self.manager.deployment_target()
                decision = self.claim(
                    plan, f"deploy:{item['revision']}", f"Deploying server {plan['version']}",
                    lambda: self.deploy(target, executable, request),
                )

                if decision.state == "waiting":
                    decision.detail = f"{decision.detail}. {item['detail']}"

                return decision

        if not executable and run.get("conclusion") == "success" and run.get("headSha") == item["revision"]:
            attention = (
                f"Deploy or verify the published server release {plan['version']}. "
                f"Client promotion resumes after live verification. {run['url']}",
            )
            return Decision("waiting", f"server: {item['detail']}", attention=attention)

        return Decision("waiting", f"server: {item['detail']}")

    def decide(self, plan, observations):
        if all(item["stage"] == "complete" for item in observations.values()):
            return Decision("complete", "Delivery verified and release branches synchronized")

        if any(item["action"] == "merge" for item in observations.values()) and not self.options.merge:
            return Decision("blocked", "A PR is ready to merge. Resume with --merge or merge it in GitHub")

        waiting = []
        attention = []

        for name, item in observations.items():
            if item["stage"] == "complete":
                continue

            gate = item.get("gate")
            detail = f"{name}: {item['detail']}"
            run = item.get("run") or {}

            if run.get("status") == "completed" and item["stage"] in ("published", "publishing"):
                conclusion = run.get("conclusion")

                if conclusion in ("cancelled", "timed_out"):
                    if run.get("headSha") != item.get("revision"):
                        return Decision("blocked", f"{name}: workflow belongs to a different release commit")

                    decision = self.retry_workflow(plan, name, run)

                    if decision.state != "waiting":
                        return decision

                    waiting.append(decision.detail)
                    continue

                if conclusion != "success":
                    return Decision(
                        "blocked", f"{name}: release workflow ended with {conclusion or 'no conclusion'} ({run['url']})",
                    )

                if name != "server" and item["stage"] == "published":
                    return Decision("blocked", f"{detail} ({run['url']})")

            if name == "server" and item["stage"] == "published":
                decision = self.server_decision(plan, item)

                if decision.state != "waiting":
                    return decision

                waiting.append(decision.detail)
                attention.extend(decision.attention)
                continue

            behind = item["stage"] == "prepare" and gate == "behind"
            if self.options.merge and name != "workspace" and (gate == "preparation_drift" or behind):
                if self.manager.refresh_preparation(plan, name):
                    component = plan["components"][name]
                    return Decision(
                        "progress", f"{name}: updated preparation to {component['prepared_head'][:12]} "
                        f"for development {component['accepted_development'][:12]}",
                    )

            if gate in REPAIR_GATES and self.options.repair:
                from release_repair import propose_repair

                key = f"repair:{name}:{item['pr']}:{item['head']}:{item['base']}:{gate}"
                decision = self.claim(
                    plan, key, f"Requesting a Codex repair for {detail}",
                    lambda: propose_repair(self.manager, plan, item, key),
                )

                if decision.state != "waiting":
                    return decision

                waiting.append(decision.detail)
                attention.append(decision.detail)
            elif gate in WAITING_GATES or item["stage"] in ("waiting", "publishing", "published", "verified"):
                if item["stage"] != "verified" and (item["stage"] != "waiting" or not waiting):
                    waiting.append(detail)

                if gate in ("review_pending", "review_threads"):
                    attention.append(detail)
            else:
                return Decision("blocked", detail)

        return Decision("waiting", " | ".join(waiting) or "Waiting for release verification", attention=tuple(attention))

    def retry_workflow(self, plan, name, run):
        key = f"retry:{name}:{run['databaseId']}"
        attempts = plan.setdefault("automation", {})
        attempt = attempts.get(key)

        if attempt:
            if attempt["state"] == "started":
                return Decision("blocked", f"{name}: workflow retry was interrupted; check {run['url']}")

            if run["attempt"] == attempt["attempt"] and time.time() - attempt["requested_at"] < 300:
                return Decision("waiting", f"{name}: waiting for workflow retry {run['url']}")

            return Decision("blocked", f"{name}: workflow is {run['conclusion']} after a retry ({run['url']})")

        attempts[key] = {"state": "started", "attempt": run["attempt"], "requested_at": time.time()}
        self.manager.save(plan)
        self.manager.fetched.clear()
        self.manager.retry(plan, name, "release", expected_run=run)
        attempts[key]["state"] = "finished"
        self.manager.save(plan)
        return Decision("progress", f"{name}: retried workflow {run['url']}")

    def tick(self):
        with self.manager.lock():
            plan = self.manager.load(self.options.release)
            self.manager.fetched.clear()
            observations = self.manager.status(plan)

            if self.options.dry_run:
                return plan, observations, Decision("preview", "Current release status")

            _, actions = self.manager.advance(plan, merge=self.options.merge, observations=observations)

            if actions:
                return plan, observations, Decision("progress", "\n".join(actions))

            return plan, observations, self.decide(plan, observations)

    def finish_job(self, release_id, key, job):
        try:
            detail = job()
            state = "finished"
        except ReleaseChanged as error:
            detail = str(error)
            state = "stale"
        except (ReleaseError, DeploymentError, OSError, subprocess.TimeoutExpired) as error:
            detail = str(error)
            state = "failed"

        with self.manager.lock(blocking=True):
            plan = self.manager.load(release_id)
            plan["automation"][key] = {"state": state, "detail": detail}
            self.manager.save(plan)

        return Decision("blocked" if state == "failed" else "progress", detail)

    def run(self):
        with self.manager.lock("runner"):
            try:
                return self.poll()
            except Exception:
                self.notify_decision(Decision("error", "The release command failed. See the runner log for details."))
                raise

    def notify_decision(self, decision):
        if not self.notify or self.options.dry_run:
            return

        if decision.state in TERMINAL_STATES:
            self.notify(self.release_id, decision.state, decision.detail)

        for detail in decision.attention:
            self.notify(self.release_id, decision.state, detail)

    def poll(self):
        options = self.options
        interval = options.poll_seconds
        deadline = time.monotonic() + options.wait_timeout if options.wait_timeout else None
        previous = None
        errors = 0

        while True:
            try:
                plan, observations, decision = self.tick()
                self.release_id = plan["id"]
                errors = 0
            except (ReleaseError, subprocess.TimeoutExpired) as error:
                errors += 1

                if errors > 5 or not (
                    isinstance(error, (ReleaseChanged, subprocess.TimeoutExpired)) or TRANSIENT_ERRORS.search(str(error))
                ):
                    raise

                observations = {}
                decision = Decision("waiting", f"Temporary release error ({errors}/5): {error}")

            if decision.job:
                self.emit(self.release_id, decision, observations)
                decision = self.finish_job(self.release_id, *decision.job)

            current = decision.state, decision.detail

            if current != previous or decision.state == "progress":
                self.emit(self.release_id, decision, observations)
                if errors <= 1:
                    interval = options.poll_seconds

            previous = current
            self.notify_decision(decision)

            if decision.state in ("complete", "preview"):
                return 0

            if decision.state == "blocked":
                return 2

            if deadline is not None and time.monotonic() >= deadline:
                timeout = Decision("timeout", f"Release wait timeout expired. Last status: {decision.detail}")
                self.emit(self.release_id, timeout, observations)
                self.notify_decision(timeout)
                return 3

            if decision.state == "progress":
                continue

            delay = min(interval, deadline - time.monotonic()) if deadline is not None else interval
            time.sleep(max(0, delay))
            interval = min(interval * 2, max(120, options.poll_seconds))


def run_release(manager, options):
    from release_manager import render

    def emit(release_id, decision, observations):
        if decision.state == "preview" and not options.json:
            render({"id": release_id}, observations, dry_run=True, merge=options.merge)
        elif options.json:
            print(json.dumps({
                "release": release_id, "state": decision.state, "detail": decision.detail,
                "components": list(observations.values()),
            }), flush=True)
        else:
            print(f"{release_id} [{decision.state}] {decision.detail}", flush=True)

    notify = ReleaseNotifications(manager.storage, options.notify_command).send if options.notify_command else None

    try:
        return Runner(manager, options, emit, notify=notify).run()
    except KeyboardInterrupt:
        return 130
