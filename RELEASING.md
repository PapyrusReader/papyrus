# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

## Release the client and server with Octopus

1. Merge the client and server changes you want to ship into their respective
   `development` branches. Octopus captures those commits when release preparation starts.
2. Open [Release Papyrus with Octopus](https://github.com/PapyrusReader/papyrus/actions/workflows/octopus.yml)
   in GitHub and select **Run workflow**. Choose **master**, select the version
   increment, and leave the explicit number empty for a calculated version and build.
3. Select **release** and click **Run workflow**. The default **preview** mode
   only calculates the version and checks Octopus access. From the CLI,
   `tools/release-octopus minor release` starts the same workflow.
4. Review the bot's **Release Papyrus** PR in the workspace. Check the version,
   Android build number, and pinned client, server and reader revisions. Under
   **Files changed → Review changes**, choose **Approve** and submit the review.
   The release account merges it after required checks pass.
5. Open the workflow run's **Summary** and follow the Octopus task link. The
   GitHub launcher finishes after dispatch. The Octopus task reports the outcome
   of production deployment and delivery.

The server image and signed client downloads build in parallel in their owning
GitHub repositories. Before deploying the new image by digest, Hetzner uploads an encrypted backup
to Cloudflare R2, downloads it, and restores PostgreSQL in an isolated container.
After deployment, Octopus checks the live server revision and migration heads.
Only then are the sealed client downloads published. Web and Play internal delivery
run independently. The separate website release stays independent.

Use **Retry** on the failed Octopus step after fixing the cause. The release host
stores the GitHub run IDs under `/var/lib/papyrus-release/octopus/<version>+<build>/`.
Retries follow or rerun the original failed GitHub jobs. For restore drills and recovery keys, see [production R2 backups](tools/backup/R2_BACKUPS.md).
The **Verify release connection** runbook checks the Octopus worker, SSH and GitHub
connection without creating a release.

The release trigger uses GitHub OIDC restricted to the workspace release workflow on
`master`. Octopus stores a dedicated SSH credential restricted on Hetzner to the named
release stages. Signing, Play credentials, and backup credentials stay with their
existing owners. The Octopus process definition is in `tools/octopus/configure.py`.

## Updating the release host

The Octopus worker connects to the forced SSH command in
`/opt/papyrus-release/octopus/ssh-entry`. Hetzner runs the installed Python
modules in that directory and `/opt/papyrus-release/lib`. Merging a workspace
PR updates the source repository. Installing these modules is a separate host
operation. Between deployments, install the matching pipeline and its imports
together and run **Verify release connection** in Octopus. Preserve the state in
`/var/lib/papyrus-release/octopus/` so that retries find the original build runs.

## GitHub Actions and release artifacts

The workspace workflow starts Octopus. The client and server `release.yml`
workflows run on dispatch against the approved workspace manifest. The client
build collects the Android, web, Linux and Windows outputs into `sealed-release`, retained for 30 days.
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
