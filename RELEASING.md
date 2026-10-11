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

## 0.0.2 delivery validation

Validation on 2026-10-11:

- Native Windows and Ubuntu 24.04/Debian 13 checks pass on client `0.0.2+2`
  final preparation commit `183bc88` (Linux run `38095826469`, Windows run
  `38095826488`). Debian 13 runs in a native x64 container on the Ubuntu runner. Packaged EPUB/PDF rendering, offline import,
  reader resume and guest/account/server-profile isolation pass. Installer
  upgrade/removal/reinstall preserve the test library and settings; Windows also
  preserves a namespaced credential and restores a quarantined WebView2 runtime.
  The Windows runner is not a clean OS image; headless Linux does not test a
  desktop Secret Service credential store.
- Server `v0.0.2` was published from `e130164` and deployed by its verified image
  digest. A fresh encrypted database/media/configuration backup was checked
  before migration. All 20 existing application tables retained identical row
  fingerprints across `af0fea8d6317` → `b5c6d7e8f901`, and `alembic check` reports
  no drift. Public API version/health, tracking schema 2 and PowerSync readiness
  pass. The publication includes all 12 sync tables. Database and static-web
  containers were not restarted.
- Publication SQL must be streamed from the released host file during an upgrade:
  a running database container may still see the old inode through its individual
  file bind mount. The regression-tested correction from server PRs #19/#20
  is installed from master `52c427b`; the published application image remains
  the unchanged `e130164` build. Master was synchronized back into development.
- Client `v0.0.2+2` was published from `6fcdb7c` in run `38096425056`.
  All four builds, GitHub publication, web deployment and Play internal delivery
  passed. Every downloaded asset matches the published manifest/checksum index.
  Delivery-only retry `38097044061` passed without rebuilding or changing any
  published asset; Play recognized the matching completed internal release.
- Public web metadata, `/`, `/login`, `flutter_bootstrap.js` and `main.dart.js`
  match the published archive byte-for-byte. The login screen renders in Chrome.
  Cloudflare's default browser TTL initially replaced JavaScript revalidation
  with four-hour caching; an app-host-only rule now bypasses its edge cache and
  respects origin browser TTL. All five public paths return `no-cache`. Client
  PRs #53/#54 add regression coverage for cache-header drift and stale compiled
  apps, using canonical browser URLs without cache-bypassing requests. The
  verifier is installed from client master `9292922`; application artifacts
  remain the unchanged `6fcdb7c` build.
  The previous web directory remains available. API, PowerSync, both databases
  and web container identities/start times remained unchanged during delivery.
- Website `v1.1.2` was published/deployed from `5e4aa8c` in run `38097027158`;
  public metadata confirms that revision. Website and client release histories
  were synchronized back to development using PRs.
- A connected Samsung SM-S928B updated through Play to `versionName=0.0.2`,
  `versionCode=2`; both installer and initiating source are
  `com.android.vending`. Its original October 6 installation timestamp remains;
  the app process runs without fatal entries in its current crash log. No app
  data was cleared or sideloaded. One tester account reported Item not found;
  a second already-authorized tester account successfully installed the update.
