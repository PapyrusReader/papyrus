# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

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

To start a coordinated release on the configured Hetzner host, pass the approved
version and Android build number. Press Ctrl-C to detach from the journal while
the release continues. Use `logs` with the same arguments to see the latest 50
lines and follow updates.

```sh
tools/release-host 0.5.1 10
tools/release-host logs 0.5.1 10
```

Status names the pending or failed CI jobs and prints a compact `Next:` instruction.
`advance --dry-run --merge` previews eligible merges without changing anything.
Without `--merge`, dry-run output explains that ready PRs will remain open.
JSON output includes the same next instruction in `next` and a `dry_run` flag.

### Run a saved release to completion

```sh
tools/papyrus release run papyrus-0.0.5+5 --dry-run --merge
tools/papyrus release run papyrus-0.0.5+5 --merge --repair
tools/papyrus release run papyrus-0.0.5+5 --merge --repair \
  --deploy-server /usr/local/bin/papyrus-release-server
```

`run` uses the same release plan and checks as `advance`. It advances eligible
steps immediately and polls while checks, review, publication, or delivery are
pending. It finishes after delivery verification and synchronization. Use the
full release ID when multiple builds share a version. `--dry-run` takes one
snapshot. `--poll-seconds` sets the initial interval (30 seconds by default),
and `--wait-timeout` limits how long the runner waits. `--json` prints one JSON
object per event. Exit codes are 0 for completion or preview, 1 for command
errors, 2 for a blocker requiring intervention, and 3 for wait timeout.

Use `--notify-command /path/to/notifier` to alert the release operator. The runner
sends one JSON object on standard input with `release`, `state`, and `detail`.
The command is called for failures, timeouts, and waits that need attention,
including PR review and manual deployment. Routine CI and publication waits stay
quiet. Successful alerts are recorded in the local release directory to suppress
duplicates across polling and restarts. Delivery failures are logged separately
from the release status. The Hetzner service uses the existing Papyrus monitoring
SMTP sender and alert recipient. Dry runs suppress alerts.

`--merge` authorizes eligible merges for that invocation. Without it the
runner stops when a PR is ready to merge. GitHub review requirements still
apply. While a component version-preparation PR is open, the merging runner
can bring it up to date with PRs merged into `development`. It verifies the
merged PRs, unchanged component version, and clean Git merge result, then waits
for checks and applicable reviews on the updated commit. It recognizes the
same branch update after a restart. Direct pushes, rewritten history,
conflicts, and independent edits to the preparation branch require inspection. A cancelled or timed-out release workflow is retried once for the same
commit. A failed publication workflow is reported with the linked run.

`--repair` uses an authenticated local `codex` CLI for a failed PR check or a
reproduced merge conflict. It creates a disposable worktree at the recorded
commit and opens a draft PR with the proposed repair. Mark the draft ready and
review it through the normal PR workflow. Use a merge commit for conflict repairs
that carry both branch histories. A repair that changes an accepted release head
still needs `accept-head` after its scope is reviewed. The runner waits for the
PR state to change and proposes one repair for each observed PR/head/base/failure
combination.

Without `--deploy-server`, the runner waits for the configured live server check
to pass after deployment. With it, the runner calls the named executable on the
configured deployment SSH host after exact-revision publication succeeds and the
healthy API reports an older version. The request contains the release version,
full commit, and published image digest. The [server release job](server/deploy/README.md)
checks the backup and restore, deploys the digest, and runs migrations. The runner
then independently verifies the live server before advancing client promotion.
Configure the target once with `verify` as documented below.

Run from a persistent checkout to survive a restart. Start `run` again with the
same release ID and options. A local lock prevents two runners in one checkout,
and the plan records repair, deployment, and workflow-retry attempts. If a job
fails or its result is unknown after interruption, inspect GitHub or the host log
before recovery. The runner will still continue if fresh checks show that the
blocked step was completed independently.

### Back up to the operator's Mac before deployment

On the deployment host, install `tools/release_backup_host.py` as
`/etc/papyrus-release/backup-check`, owned by root with mode 0700. Configure the
server deployment job to use this hook. Add
`--deploy-server /usr/local/bin/papyrus-release-server` to the runner service's
existing launch arguments and reload systemd. Apply service changes between
deployment jobs.

On the Mac, install restic and use Python 3.11 or later. The worker uses the
existing `hetzner` SSH configuration with batch authentication and local Docker.
Install the launch agent once from the logged-in operator's terminal:

```sh
python3 tools/release_backup_mac.py install
```

The host stages the media before briefly stopping the API for a final copy and
database dump. It restarts the API before transferring data. The Mac polls every
two minutes, saves the archive in the encrypted restic repository at
`~/Backups/papyrus/release.restic`, and reads it back. It checks the entire
archive against the host digest, restores PostgreSQL into a disposable container,
and compares the counts and migration revision. The host proceeds only after
receiving confirmation for that specific backup. The Mac must be awake with its
operator logged in and Docker available. After 45 minutes without confirmation
the hook stops the deployment. Worker logs are in `~/Library/Logs/Papyrus/`.
Keep the repository's recovery password at
`~/.config/papyrus-backup-keys/release-restic.password` with the backup keys.

### Inspect status and delivery manually

The component stages are `planned`, `prepare`, `prepared`, `promote`, `publishing`,
`published`, and `verified`. `waiting` and `blocked` explain unmet prerequisites.
Once components and the workspace snapshot are verified, `sync` brings release
history back into development. `complete` requires that history, verified delivery,
and a fresh successful server deployment check.
Synchronization uses a PR when ancestry requires it, preserving protected-branch
review. Already synchronized repositories do not get another PR.

Server publication does not deploy the server. Deploy the exact image and migrations
using the existing server deployment procedure, then configure the read-only check:

```sh
tools/papyrus release verify 0.0.5 --component server \
  --revision FULL_RELEASE_COMMIT_SHA \
  --server-url https://api.papyrus-reader.com \
  --ssh-host hetzner --container papyrus-production-api-1
```

Use the SSH alias and API container for your deployment. The checker requires
HTTPS and read-only SSH access to Docker. It checks public API health and version,
container health, image revision and published digest, installed package version,
and the database migration heads from the released source. It never deploys or
runs migrations. SSH commands use batch mode and bounded timeouts.

The target is saved locally in ignored `.local/release-deployment.json`, even if
verification fails, so the next status check can explain the actual mismatch.
It is not archived or copied into PRs. `status`, `list`, and `advance` repeat the
live checks. Missing configuration, failed checks, and unreachable production
leave the server `published`, prevent completion, and block client promotion.
An older release that is no longer deployed is also reported as `published`.
Existing free-text attestations never count as deployment proof. `--evidence`
is an optional note saved only after checks pass and cannot override their result.

Client and website delivery are verified automatically only when the release
workflow and its actual delivery verification steps succeeded. A successful run
with skipped delivery jobs is insufficient. `verify` can record a note only after
those checks pass. Do not include credentials in notes, which are archived with
the release.

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
inputs. A delivery-only retry alone does not clear the release gate. Rerun the
failed jobs of the original release workflow so its verification steps pass.

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
release, an overall status, and the component states. It combines local plans
and GitHub releases, showing the newest 20 by publication time or
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
Plans remain local for coordinated and standalone website releases. The workspace
snapshot records component gitlinks and `release.json`; it does not commit plans
or release-note copies. GitHub releases and PRs hold publication notes and delivery
evidence. A fresh checkout discovers published releases from GitHub; resuming
release mutations requires the original local plan.

The lower-level `tools/release.py check` and `bump` commands remain available for
manual workflows and CI.

## Prepare a release manually

From the workspace root, on clean release-preparation branches based on
`development` in the workspace, client and server. The version numbers below are
illustrative; choose the next release's version and an increasing Android code:

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
