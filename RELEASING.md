# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

## Release the client and server with Octopus

In the workspace GitHub Actions tab, select **Release Papyrus with Octopus**, choose
the `master` branch, and select a major, minor, or patch bump. The default **preview**
mode reads the current version and checks the Octopus connection. **Release** starts
the pipeline. From an authenticated GitHub CLI, use `tools/release-octopus minor preview`
or `tools/release-octopus minor release`. The GitHub run links the Octopus task.

Octopus records the reviewed development tips once, writes version-only commits on
component release branches, and opens one workspace manifest PR against `master`.
The PR contains the version, Android build number, client/server gitlinks, and the
reader revision in the client lockfile. Review the snapshot there. The release
account merges the approved PR when its required checks pass. Later changes on
component development branches are picked up by the next release.

The server image and signed client downloads build in parallel in their owning
GitHub repositories. The server job requires a verified off-host backup and
restore, deploys by digest, and verifies the running revision and migration heads.
Only then are the sealed client downloads published. Web and Play internal delivery
run independently. The separate website release stays independent.

Use **Retry** on the failed Octopus step after fixing the cause. The release host
stores the GitHub run IDs under `/var/lib/papyrus-release/octopus/<version>+<build>/`.
Retries follow or rerun the original failed GitHub jobs. Signed client build artifacts
are retained for 30 days until publication. The installed backup hook selects the storage destination.
See [R2 backup migration](tools/backup/R2_MIGRATION.md) for cutover and recovery. The **Verify release connection** runbook checks
worker, SSH, and GitHub connectivity without creating a release.

The release trigger uses GitHub OIDC restricted to the workspace release workflow on
`master`. Octopus stores a dedicated SSH credential restricted on Hetzner to the named
release stages. Signing, Play credentials, and backup credentials stay with their
existing owners. The Octopus process definition is in `tools/octopus/configure.py`.

## GitHub Actions and release artifacts

The workspace workflow starts Octopus. The client and server `release.yml`
workflows run on dispatch against the approved workspace manifest. The client
build collects the Android, web, Linux and Windows outputs into `sealed-release`.
Publication downloads that artifact from the recorded build run. Delivery reads
the published assets by tag. Workspace version, tooling and deployment-layout
checks validate pull requests. They have no publish or deploy steps.

Use Octopus task history for coordinated releases. Historical GitHub release
assets and server images remain available for client installation and recovery.

## Independent website releases

```sh
tools/papyrus release plan 1.1.8 --component website
tools/papyrus release prepare website-1.1.8
tools/papyrus release advance website-1.1.8 --merge
```

The website has its own version, release workflow and deployment checks. The
release CLI also supports read-only `list` and `status` for historical releases.
Saved plans are in `.local/releases/`.

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
