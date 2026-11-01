"""Compare successful GitHub Actions runs using elapsed and aggregate job seconds."""

import argparse
import json
import statistics
import subprocess
from datetime import datetime

WORKFLOWS = {"client": "ci.yml", "server": "ci.yml", "reader": "ci.yml", "workspace": "tooling.yml"}


def github(path, *, paginate=True, **parameters):
    command = ["gh", "api", "--method", "GET", path]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    for key, value in parameters.items():
        command.extend(["-f", f"{key}={value}"])
    result = json.loads(subprocess.check_output(command, text=True))
    return result if paginate else [result]


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def measure(run, jobs):
    if run["status"] != "completed" or run["conclusion"] != "success":
        raise ValueError(f"Run {run['id']} has not completed successfully")
    active = [job for job in jobs if job["conclusion"] != "skipped"]
    if not active or any(
        job["status"] != "completed" or not job.get("started_at") or not job.get("completed_at")
        for job in active
    ):
        raise ValueError(f"Run {run['id']} has incomplete job timing data")
    measured = []
    for job in active:
        started, ended = timestamp(job["started_at"]), timestamp(job["completed_at"])
        if ended < started:
            raise ValueError(f"Job {job['name']} has invalid timing data")
        steps = [
            {
                "name": step["name"],
                "seconds": (timestamp(step["completed_at"]) - timestamp(step["started_at"])).total_seconds(),
                "conclusion": step["conclusion"],
            }
            for step in job.get("steps", [])
            if step.get("started_at") and step.get("completed_at") and step["conclusion"] != "skipped"
        ]
        measured.append({
            "name": job["name"], "labels": sorted(job.get("labels", [])),
            "seconds": (ended - started).total_seconds(), "steps": steps,
        })
    elapsed = (
        max(timestamp(job["completed_at"]) for job in active)
        - min(timestamp(job["started_at"]) for job in active)
    ).total_seconds()
    return {
        "id": run["id"], "url": run["html_url"], "commit": run["head_sha"],
        "branch": run["head_branch"], "event": run["event"], "attempt": run["run_attempt"],
        "workflow_id": run["workflow_id"], "elapsed_seconds": elapsed,
        "runner_seconds": sum(job["seconds"] for job in measured), "jobs": measured,
    }


def collect(repository, workflow, branch, event, samples):
    pages = github(
        f"repos/{repository}/actions/workflows/{workflow}/runs",
        branch=branch, event=event, status="success", per_page=samples, paginate=False,
    )
    runs = [run for page in pages for run in page["workflow_runs"]][:samples]
    results = []
    for run in runs:
        pages = github(f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", per_page=100)
        results.append(measure(run, [job for page in pages for job in page["jobs"]]))
    return results


def groups(runs):
    grouped = {}
    for run in runs:
        signature = tuple(sorted((job["name"], tuple(job["labels"])) for job in run["jobs"]))
        key = (run["workflow_id"], run["branch"], run["event"], signature)
        grouped.setdefault(key, []).append(run)
    return list(grouped.values())


def markdown(runs):
    lines = [
        "### CI timing", "",
        "Each row measures only the displayed run attempt. Successful runs only. Elapsed spans first job start to last job completion, excluding the initial queue.",
        "Runner time sums job durations, including parallel jobs. It is unweighted time, not billed minutes.",
        "Medians are grouped by workflow, branch, event, job names and runner labels. Cache and runner-image differences may still affect comparisons.",
        "", "| Run | Branch | Event | Attempt | Elapsed (s) | Runner (s) |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for run in runs:
        branch = run["branch"].replace("|", "\\|")
        lines.append(
            f"| [{run['id']}]({run['url']}) | {branch} | {run['event']} | {run['attempt']} | "
            f"{run['elapsed_seconds']:.1f} | {run['runner_seconds']:.1f} |"
        )
    for group in groups(runs):
        first = group[0]
        elapsed = statistics.median(run["elapsed_seconds"] for run in group)
        runner = statistics.median(run["runner_seconds"] for run in group)
        names = ", ".join(job["name"] for job in first["jobs"])
        evidence = "single observation" if len(group) == 1 else f"{len(group)} observations"
        lines.extend([
            "", f"{first['branch']} / {first['event']} ({names}): {evidence}, "
            f"median elapsed {elapsed:.1f}s, median runner {runner:.1f}s.",
        ])
    if not runs:
        lines.extend(["", "No successful runs matched the selection."])
    lines.extend(["", "Use several comparable runs before attributing changes to an optimization. JSON output includes per-job and per-step timings."])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Example: tools/papyrus ci-timing reader --branch development --event push --samples 5. "
        "Repeat --branch to compare separate groups. Use --event pull_request for PR branches.",
    )
    parser.add_argument("component", choices=WORKFLOWS)
    parser.add_argument("--workflow", help="Workflow filename, defaults to the component's main checks")
    parser.add_argument("--branch", action="append", help="Repeat for separate comparison groups, default: development")
    parser.add_argument("--event", choices=["push", "pull_request", "workflow_dispatch"], default="push")
    parser.add_argument("--samples", type=int, default=5, help="Recent successful runs per branch (1-30)")
    parser.add_argument("--json", action="store_true", help="Include per-job and per-step timings as JSON")
    options = parser.parse_args(argv)
    if not 1 <= options.samples <= 30:
        parser.error("--samples must be between 1 and 30")
    repository = "PapyrusReader/" + ("papyrus" if options.component == "workspace" else options.component)
    runs = []
    try:
        for branch in dict.fromkeys(options.branch or ["development"]):
            runs.extend(collect(repository, options.workflow or WORKFLOWS[options.component], branch, options.event, options.samples))
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        parser.exit(1, f"Cannot read CI timing: {error}\n")
    print(json.dumps(runs, indent=2) if options.json else markdown(runs), end="\n" if options.json else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
