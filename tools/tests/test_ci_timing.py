import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ci_timing", Path(__file__).resolve().parents[1] / "ci_timing.py")
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


def job(name, start, end):
    return {"name": name, "started_at": f"2026-10-29T12:00:{start:02d}Z",
            "completed_at": f"2026-10-29T12:00:{end:02d}Z", "status": "completed",
            "conclusion": "success", "labels": ["ubuntu-latest"], "steps": []}


RUN = {"id": 1, "status": "completed", "conclusion": "success", "html_url": "https://example.test/run/1",
       "head_sha": "abc", "head_branch": "development", "event": "push", "run_attempt": 2, "workflow_id": 3}


class TimingTest(unittest.TestCase):
    def test_parallel_jobs_and_between_job_queue_are_distinct_from_runner_time(self):
        report = timing.measure(RUN, [job("tests", 0, 20), job("browser", 0, 10), job("gate", 25, 30)])
        self.assertEqual(report["elapsed_seconds"], 30)
        self.assertEqual(report["runner_seconds"], 35)
        self.assertEqual(report["attempt"], 2)

    def test_skipped_jobs_have_no_runner_cost_and_partial_data_is_rejected(self):
        skipped = {"conclusion": "skipped", "started_at": None, "completed_at": None}
        self.assertEqual(timing.measure(RUN, [job("tests", 0, 20), skipped])["runner_seconds"], 20)
        with self.assertRaises(ValueError):
            timing.measure(RUN, [dict(job("tests", 0, 20), completed_at=None)])
        with self.assertRaises(ValueError):
            timing.measure(dict(RUN, conclusion="failure"), [job("tests", 0, 20)])
        with self.assertRaises(ValueError):
            timing.measure(RUN, [skipped])

    def test_pagination_uses_only_selected_attempt_and_limits_history_queries(self):
        pages = [[{"workflow_runs": [RUN]}], [{"jobs": [job("tests", 0, 20)]}, {"jobs": [job("browser", 0, 10)]}]]
        with patch.object(timing, "github", side_effect=pages) as github:
            reports = timing.collect("PapyrusReader/client", "ci.yml", "development", "push", 5)
        self.assertEqual(reports[0]["runner_seconds"], 30)
        self.assertFalse(github.call_args_list[0].kwargs["paginate"])
        self.assertIn("/attempts/2/jobs", github.call_args_list[1].args[0])

    def test_comparison_groups_separate_event_runner_and_job_changes(self):
        baseline = timing.measure(RUN, [job("tests", 0, 20)])
        observations = [baseline, copy.deepcopy(baseline)]
        for field, value in [("event", "pull_request"), ("branch", "master"), ("workflow_id", 4)]:
            observations.append(dict(baseline, **{field: value}))
        changed_runner = copy.deepcopy(baseline)
        changed_runner["jobs"][0]["labels"] = ["windows-latest"]
        observations.append(changed_runner)
        observations.append(timing.measure(RUN, [job("tests", 0, 20), job("browser", 0, 10)]))
        self.assertEqual(len(timing.groups(observations)), 6)
        report = timing.markdown(observations)
        self.assertIn("2 observations, median elapsed 20.0s, median runner 20.0s", report)
        self.assertIn("single observation", report)


if __name__ == "__main__":
    unittest.main()
