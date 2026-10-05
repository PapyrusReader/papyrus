# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

For the initial internal test, the coordinated version is `0.0.1`, Android build
`1`. This marks an early testing release. No reader version or reader implementation
is changed by this policy.

## Prepare a release

From the workspace root, on clean release branches:

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

1. Create component PRs and run checks appropriate to the changed behavior.
2. Merge the server PR first if both versions changed. Its workflow publishes the
   versioned GHCR image; deploy and verify it before rolling out the client.
3. Merge the client PR. Its workflow compares the actual committed version with
   the previous push and builds signed Android AAB/web/Linux/Windows artifacts.
4. Update the workspace component pointers to the reviewed commits and commit
   `release.json`. The workspace release CI checks **recorded gitlinks**, not a
   contributor's dirty working copies. A coordinated workspace PR becomes valid
   when its recorded client/server versions agree with the release snapshot.

These are independent Git histories, so there is no atomic multi-repo merge.
The workspace validates the final snapshot; it does not automatically deploy a
new server or publish a Play release. Dependency-only edits to a manifest do not
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
