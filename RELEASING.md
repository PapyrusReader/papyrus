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

## Prepare a release

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
