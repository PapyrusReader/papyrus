"""Release discovery, status aggregation, and compact catalog output."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_catalog import Catalog, render_list, summary  # noqa: E402
from release_manager import GitHub, Manager, ReleaseError, main, step  # noqa: E402


def publication(tag, date="2026-10-24T12:00:00Z", **fields):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": date,
        "created_at": date,
        "html_url": f"https://github.com/test/releases/tag/{tag}",
        **fields,
    }


class CatalogTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Mock(root=self.root, storage=self.root / ".local/releases")
        self.manager.repo.side_effect = lambda name: f"test/{name}"
        self.publications = {"server": [], "client": [], "website": []}
        self.manager.github.releases.side_effect = lambda repo: self.publications[repo.split("/")[1]]
        self.manager.revision.return_value = "a" * 40
        self.manager.matches.return_value = True
        self.manager.ancestor.return_value = True
        self.manager.publication.side_effect = lambda record, name, revision: step(
            name,
            "published" if name == "server" else "verified",
            revision=revision,
        )
        self.catalog = Catalog(self.manager)

    def save(self, release_id, directory=None, **fields):
        target = directory or self.manager.storage
        target.mkdir(parents=True, exist_ok=True)
        version = release_id.split("-", 1)[1].split("+")[0]
        plan = {"id": release_id, "version": version, "components": {"website": {}}, **fields}
        (target / f"{release_id}.json").write_text(json.dumps(plan))
        return plan

    def test_remote_versions_are_grouped_and_rebuilds_remain_distinct(self):
        self.publications["server"] = [publication("v0.0.5"), publication("v0.0.4")]
        self.publications["client"] = [publication("v0.0.4+4"), publication("v0.0.4+5")]
        self.publications["website"] = [publication("v1.1.7")]
        rows = self.catalog.list()
        indexed = {row["release"]: row for row in rows}
        self.assertEqual(set(indexed), {"server-0.0.5", "papyrus-0.0.4+4", "papyrus-0.0.4+5", "website-1.1.7"})
        self.assertEqual(indexed["papyrus-0.0.4+4"]["status"], "published")
        self.assertEqual(indexed["website-1.1.7"]["status"], "verified")
        self.assertEqual(self.manager.publication.call_count, 5)
        self.manager.save.assert_not_called()
        self.assertFalse(self.manager.storage.exists())

    def test_local_plan_overrides_archive_and_github_without_duplicate_rows(self):
        self.save("website-1.1.7", self.root / "releases", verification="old")
        plan = self.save("website-1.1.7", verification="current")
        self.publications["website"] = [publication("v1.1.7")]
        self.manager.status.return_value = {"website": step("website", "complete")}
        rows = self.catalog.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "complete")
        self.assertEqual(rows[0]["source"], "local")
        self.manager.status.assert_called_once_with(plan)
        self.manager.publication.assert_not_called()

    def test_missing_server_is_distinct_from_discovery_failure(self):
        self.publications["client"] = [publication("v0.0.4+4")]
        rows = self.catalog.list("papyrus")
        self.assertEqual(rows[0]["status"], "incomplete")
        self.assertEqual(rows[0]["components"][0]["stage"], "missing")

        def fail_server(repo):
            if repo == "test/server":
                raise ReleaseError("HTTP 403")

            return self.publications[repo.split("/")[1]]

        self.manager.github.releases.side_effect = fail_server
        catalog = Catalog(self.manager)
        rows = catalog.list("papyrus")
        self.assertEqual(rows[0]["status"], "unknown")
        self.assertEqual(rows[0]["components"][0]["stage"], "unknown")
        self.assertIn("403", catalog.errors[0])

    def test_one_unavailable_release_does_not_hide_other_releases(self):
        self.publications["website"] = [publication("v1.1.6"), publication("v1.1.7")]

        def observe(record, name, revision):
            if record["version"] == "1.1.6":
                raise ReleaseError("network failure")

            return step(name, "verified")

        self.manager.publication.side_effect = observe
        rows = self.catalog.list("website")
        self.assertEqual({row["status"] for row in rows}, {"unknown", "verified"})
        self.assertEqual(len(self.catalog.errors), 1)

    def test_drafts_do_not_require_a_published_tag(self):
        self.publications["website"] = [publication("v1.1.8", draft=True, published_at=None)]
        rows = self.catalog.list("website")
        self.assertEqual(rows[0]["status"], "draft")
        self.manager.fetch.assert_not_called()
        self.manager.publication.assert_not_called()

    def test_limit_is_applied_after_merging_and_sorting_sources(self):
        self.publications["website"] = [
            publication("v1.1.6", "2026-10-20T12:00:00Z"),
            publication("v1.1.7", "2026-10-23T12:00:00Z"),
        ]
        self.publications["server"] = [publication("v0.0.4", "2026-10-24T12:00:00Z")]
        rows = self.catalog.list(limit=2)
        self.assertEqual([row["release"] for row in rows], ["server-0.0.4", "website-1.1.7"])
        self.assertEqual(self.manager.publication.call_count, 2)

    def test_component_filter_avoids_other_repositories(self):
        self.publications["website"] = [publication("v1.1.7")]
        self.catalog.list("website")
        self.manager.github.releases.assert_called_once_with("test/website")

    def test_status_discovers_historical_release_without_creating_a_plan(self):
        self.publications["website"] = [publication("v1.0.0")]
        record, observations = self.catalog.inspect("website-1.0.0")
        self.assertEqual(record["source"], "github")
        self.assertEqual(observations["website"]["stage"], "verified")
        self.manager.create_plan.assert_not_called()
        self.manager.save.assert_not_called()

    def test_short_selector_is_rejected_when_multiple_builds_match(self):
        self.publications["client"] = [publication("v0.0.4+4"), publication("v0.0.4+5")]

        with self.assertRaisesRegex(ReleaseError, "ambiguous"):
            self.catalog.inspect("0.0.4")

        record, _ = Catalog(self.manager).inspect("papyrus-0.0.4+5")
        self.assertEqual(record["build"], 5)

    def test_short_selector_checks_for_remote_rebuilds_even_with_a_local_plan(self):
        self.save("papyrus-0.0.4+4", build=4)
        self.publications["client"] = [publication("v0.0.4+5")]

        with self.assertRaisesRegex(ReleaseError, "ambiguous"):
            self.catalog.inspect("0.0.4")

        self.manager.status.return_value = {"client": step("client", "prepare")}
        record, _ = Catalog(self.manager).inspect("papyrus-0.0.4+4")
        self.assertEqual(record["source"], "local")

    def test_network_failure_is_not_reported_as_release_not_found(self):
        self.manager.github.releases.side_effect = ReleaseError("HTTP 401")

        with self.assertRaisesRegex(ReleaseError, "Discovery incomplete.*401"):
            self.catalog.inspect("website-1.1.7")

    def test_failed_workflow_and_blocked_pr_have_explicit_summary_states(self):
        self.assertEqual(summary({"client": step("client", "published", run={"conclusion": "failure"})}), "failed")
        self.assertEqual(
            summary({"client": step("client", "blocked"), "workspace": step("workspace", "waiting")}), "blocked"
        )
        self.assertEqual(
            summary({"server": step("server", "verified"), "client": step("client", "prepare")}), "prepare"
        )
        self.assertEqual(summary({"website": step("website", "sync")}), "sync")

    def test_table_has_one_row_per_release_and_json_contains_details(self):
        self.publications["website"] = [publication("v1.1.7")]
        rows = self.catalog.list("website")

        with contextlib.redirect_stdout(io.StringIO()) as output:
            render_list(rows, [])

        self.assertEqual(len(output.getvalue().splitlines()), 2)
        self.assertIn("website-1.1.7", output.getvalue())
        self.assertIn("website:verified", output.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as output:
            render_list(rows, [], as_json=True)

        document = json.loads(output.getvalue())
        self.assertEqual(document["releases"][0]["components"][0]["stage"], "verified")
        self.assertEqual(document["errors"], [])

    def test_list_command_returns_nonzero_for_partial_results(self):
        with (
            patch("release_manager.Manager", return_value=self.manager),
            patch.object(Catalog, "list", side_effect=lambda catalog_component, limit: []),
            patch("release_catalog.Catalog", return_value=self.catalog),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.manager.lock = contextlib.nullcontext
            self.catalog.errors.append("website: network failure")
            result = main(["list", "--json"], root=self.root)

        self.assertEqual(result, 1)
        self.assertIn("network failure", json.loads(output.getvalue())["errors"][0])

    def test_remote_discovery_does_not_make_mutation_commands_accept_missing_plans(self):
        manager = Manager(self.root)

        with self.assertRaisesRegex(ReleaseError, "not found"):
            manager.load("website-1.0.0")


class GitHubDiscoveryTest(unittest.TestCase):
    def test_release_discovery_flattens_all_pages(self):
        github = GitHub(Path("."))

        with patch.object(github, "data", return_value=[[publication("v1.1.6")], [publication("v1.1.7")]]) as data:
            releases = github.releases("test/website")

        self.assertEqual([item["tag_name"] for item in releases], ["v1.1.6", "v1.1.7"])
        data.assert_called_once_with("api", "repos/test/website/releases?per_page=100", "--paginate", "--slurp")

    def test_unrelated_open_promotion_does_not_hide_an_older_published_release(self):
        github = GitHub(Path("."))

        with patch.object(github, "data", return_value=[{"body": "Next release", "state": "OPEN"}]):
            self.assertIsNone(github.find_pr("test/repo", "development", "master", "old-release", allow_unrelated=True))

            with self.assertRaisesRegex(ReleaseError, "unrelated"):
                github.find_pr("test/repo", "development", "master", "old-release")


if __name__ == "__main__":
    unittest.main()
