# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

For the initial internal test, the coordinated version is `0.0.1`, Android build
`1`. This marks an early testing release. No reader version or reader implementation
is changed by this policy.

## Integrate changes

`development` is the long-lived default branch in every repository. Ordinary
feature/fix PRs target it, without version bumps. Integration merges run checks
and accumulate work; client/server builds and website deployments remain gated
by version changes on `master`, the release branch.

Prepare version bumps only when ready to promote a batch of changes. Website
releases keep their independent version and deployment workflow. Reader and docs
follow the same integration/promotion flow; docs publish from `master`.

## Release manager

Use `tools/papyrus release` with Python 3.11+, Git, and an authenticated GitHub
CLI (`gh`). Run it from any directory. Commands print one line per component or
performed action. Add `--json` for structured output.

Interactive terminals show a single spinner with the current operation and elapsed
time. It clears before results or errors appear. Progress is disabled for `--json`,
redirected output, CI, and terminals with `TERM=dumb`.

```sh
tools/papyrus release plan 0.0.5 --android-build 5
tools/papyrus release prepare 0.0.5
tools/papyrus release status 0.0.5
tools/papyrus release advance 0.0.5 --dry-run
tools/papyrus release advance 0.0.5 --merge
```

`plan` records intent locally and inspects GitHub without creating branches or
PRs. `prepare` pushes version-preparation branches and opens PRs. `advance`
performs at most one transition per component, then exits. Run it again after
reviews or builds finish. Without `--merge`, it creates eligible PRs but does not
merge them. With `--merge`, it also merges ready PRs using merge commits, after
checking CI, review decisions, unresolved threads, and the expected PR head.
GitHub branch protections still apply. Existing GitHub review exemptions are
respected: a generic `REVIEW_REQUIRED` flag does not block a PR that GitHub reports
as `CLEAN`. Requested changes, unresolved threads, failing/pending CI, and blocked
merge states still stop the merge. The tool never uses `--admin` or infers an
exemption merely because you authored the PR.
You can also merge PRs in GitHub and resume from the CLI.

Status names the pending or failed CI jobs and prints a compact `Next:` instruction.
`advance --dry-run --merge` previews eligible merges without changing anything.
Without `--merge`, dry-run output explains that ready PRs will remain open.
JSON output includes the same next instruction in `next` and a `dry_run` flag.

The component stages are `planned`, `prepare`, `prepared`, `promote`, `publishing`,
`published`, and `verified`. `waiting` and `blocked` explain unmet prerequisites.
Once components and the workspace snapshot are verified, `sync` brings release
history back into development and `complete` means that history is present.
Synchronization uses a PR when ancestry requires it, preserving protected-branch
review. Already synchronized repositories do not get another PR.

Server publication does not deploy the server. Deploy and verify the exact image
using the existing server deployment procedure, then record that evidence:

```sh
tools/papyrus release verify 0.0.5 --component server \
  --revision FULL_RELEASE_COMMIT_SHA \
  --evidence 'Deployed image digest sha256:...; API health and migrations verified'
```

This is an operator attestation, not an automatic deployment check. It must name
the published tag's exact commit. Client promotion waits for server verification.
Client and website delivery are verified automatically only when the release
workflow and its actual delivery verification steps succeeded. A successful run
with skipped delivery jobs is insufficient. `verify` can also record independent
client or website delivery evidence, including verification after a delivery-only
retry. Do not include credentials in evidence, which is archived with the release.

```sh
tools/papyrus release retry 0.0.5 --component client --stage web
tools/papyrus release retry 0.0.5 --component client --stage play
tools/papyrus release retry 0.0.5 --component server --stage release
```

Web/Play retries dispatch the existing delivery workflow against the published
tag, without rebuilding assets. `--stage release` reruns failed jobs belonging to
the release commit. When no run exists, it can dispatch a release workflow only
if that exact commit is still master. Delivery-only runs are not automatically
attributed to a release because GitHub's run listing does not expose dispatch
inputs. Inspect the retry and record delivery evidence with `verify`.

Preparation happens in temporary detached worktrees. Existing checkouts, dirty
files, and submodule revisions are preserved. Repeating commands reuses the same
branches and PRs, including after a push succeeded but PR creation was interrupted.
Development commits are recorded in the plan. If a head changes, inspect the new
scope and explicitly accept it before proceeding:

```sh
tools/papyrus release accept-head 0.0.5 --component client
```

This accepts the current development head and, for an open preparation PR, its
current head. It does not resolve conflicts, rewrite versions, or bypass review.
Closed PRs must be reopened before the release can continue. If master advances
beyond this release before synchronization, synchronize that history separately.
There is no automatic rollback of published components.

Website releases are independent:

```sh
tools/papyrus release plan 1.1.8 --component website
tools/papyrus release prepare website-1.1.8
tools/papyrus release advance website-1.1.8 --merge
```

For a client-only rebuild, keep the semantic version and increase the Android
build. The already published server version is reused and still requires recorded
deployment verification. Full IDs distinguish rebuilds, such as
`papyrus-0.0.5+5` and `papyrus-0.0.5+6`. Short versions are accepted only when
unambiguous.

```sh
tools/papyrus release list
tools/papyrus release list --component papyrus --limit 10
tools/papyrus release list --component website --json
tools/papyrus release status papyrus-0.0.4+4
```

`list` refreshes status from GitHub and prints a compact table with one row per
release, an overall status, and the component states. It combines local plans,
archived plans, and GitHub releases, showing the newest 20 by publication time or
saved-plan modification time. `--limit` changes the row count. A saved plan takes
precedence over a discovered release with the same ID. In-progress plans use the
same live state checks as `status`.

Discovery groups a client build with its matching server version. A server version
without a matching client release or saved coordinated plan appears as
`server-<version>`. Standalone website releases remain separate. `status` accepts
these discovered IDs, including older versions, without saving a plan or changing
release branches. Mutation commands still require a saved plan.

For discovered releases, `published` confirms publication and `verified` confirms
the existing delivery verification steps passed. Neither implies that a workspace
snapshot or branch synchronization is complete without a saved plan. Server
deployment evidence cannot be inferred from publication. Drafts, missing matching
publications, failed workflows, and unavailable status data are reported distinctly.
Partial lookup failures preserve the other rows and return a nonzero exit code.
`--json` includes component details and an `errors` array.

Plans live in ignored `.local/releases/*.json`, with atomic writes and a lock
against overlapping local commands. Keep this directory to resume an in-progress
release, or copy its JSON plan to the same location in another checkout. Do not
run two machines against the same release concurrently. Status is derived from
Git refs, PRs, tags, and workflow results each time, rather than a saved stage.
A coordinated release archives its plan in `releases/<release-id>.json` as part of
the workspace snapshot PR. Archived plans can also be inspected from a checkout
containing that file. Standalone website plans remain in `.local/releases`.
The workspace snapshot updates only client/server gitlinks and release metadata.

The lower-level `tools/release.py check` and `bump` commands remain available for
manual workflows and CI.

## Prepare a release manually

From the workspace root, on clean release-preparation branches based on
`development` in the workspace, client and server:

```sh
python3 tools/release.py check
python3 tools/release.py bump 0.0.2 --android-build 2
python3 tools/release.py check
```

The bump changes the client pubspec, server pyproject and only the server's own
package entry in `uv.lock`; it does not upgrade dependencies. Review the diffs,
then run `uv sync --locked --extra dev` in the server to refresh installed package
metadata. For a client-only rebuild (new endpoint settings, signing retry after
an uploaded build, etc.), keep the semantic number and increase `--android-build`.
The unchanged server version skips its release build.

1. Open the component version-preparation PRs against `development`, run checks
   appropriate to the changed behavior, and merge them. This does not release.
2. Open `development` → `master` release PRs for the server and client. Use
   **Create a merge commit**, preserving the integration branch's history.
3. Merge the server release PR first if both versions changed. Its workflow
   publishes the versioned GHCR image; deploy and verify it before rolling out
   the client.
4. Merge the client release PR. Its workflow compares the actual committed
   version with the previous push and builds signed Android AAB/web/Linux/Windows
   artifacts, including desktop installers. After GitHub publication, independent
   jobs deploy the web app and publish Android to Play internal testing. Public
   Play rollout remains a separate process.
5. Update the workspace component pointers to the reviewed release commits and
   commit `release.json` in a PR against workspace `development`. Release CI
   checks **recorded gitlinks**, not dirty working copies. When this snapshot
   passes, promote workspace `development` to `master` with a merge-commit PR.
6. Bring each released repository's `master` back into `development` before the
   next batch of work. If `development` has no newer commits, fast-forward it to
   `master`; otherwise open a `master` → `development` synchronization PR and
   merge it with a merge commit. Keep both long-lived branches.

These are independent Git histories, so there is no atomic multi-repo merge.
The workspace validates the final snapshot; it does not automatically deploy a
new server. Client delivery is owned by the client release workflow. Dependency-only edits to a manifest do not
release. Manual workflow dispatch on `master` bootstraps the first build or retries
an unreleased commit. Existing released tags cannot be reassigned to a different
commit. Android codes are committed and monotonic, never workflow counters.
Before a repository has any release tags, its initial version can be reset without
increasing an unused Android code. Once releases exist, the gate enforces increases.

## First-device-test setup

- Follow [client release/signing and Play setup](client/docs/RELEASING.md).
- Follow [server deployment and backup setup](server/deploy/README.md).
- The registered domain is `papyrus-reader.com`. Use `api.papyrus-reader.com`,
  `sync.papyrus-reader.com` and `app.papyrus-reader.com` for the API, PowerSync and
  the verification/reset web app. Point these DNS records to the server after
  selecting its public IP.
- Build configuration belongs in the client GitHub `release` environment; server
  deployment secrets stay on the VM. No live infrastructure is provisioned by CI.
- Internal testing is the initial target. Play account/app creation, upload key,
  public endpoints and a first manual AAB upload are external prerequisites.
  Account deletion/store declarations remain prerequisites for wider distribution.

Use focused tests for the feature being released. Release-gate tests are fast
stdlib tests without Flutter or a database; native packaging gets an actual
Android build and page-size check. Full application suites remain part of existing
component PR CI, not something to rerun locally for every version-only edit.
