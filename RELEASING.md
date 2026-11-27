# Coordinated Papyrus releases

The workspace `release.json` records a release snapshot: a shared semantic client
and server version, plus the Android upload number. Component repositories remain
independent. Matching versions identify a release; they do not mean an old client
must stop working when the server advances. Keep `/v1` backwards compatible and
deploy additive server changes before distributing a client that needs them.

## Release the client and server with Octopus

Use **Run workflow** on workspace `master`, choose the version increment and
select **release**. The CLI equivalent is `tools/release-octopus minor release`.
The default **preview** calculates the number and checks both GitHub App installations and Octopus access.
[Release Papyrus with Octopus](https://github.com/PapyrusReader/papyrus/actions/workflows/octopus.yml)

1. GitHub merges outstanding component `master` changes into `development` through
   sync PRs when needed. It then reads each remote development tip and records
   both SHAs in the workspace `release-inputs/<version>+<build>` ref for retries.
2. It creates `release/<version>+<build>` at each selected component revision
   with the version edits, runs the normal PR checks and merges into component
   `master`. A separate reviewer reconstructs the expected Git commit from the
   protected history before approving that specific PR head.
3. The workspace release PR pins both actual merge commits and the reader SHA
   locked by the promoted client. Once checks and independent verification pass,
   the release account merges the manifest into workspace `master`.
4. GitHub sends that merge SHA with the Octopus release. Octopus verifies it
   against workspace `master`, then starts the builds. A separate GitHub job
   syncs each promoted commit into its current `development` branch through
   checked PRs. Follow the production task from the GitHub workflow summary.

If preparation or a sync fails, inspect the linked PR or reviewer run, fix the
reported problem and rerun the failed GitHub jobs. They reuse the frozen inputs
and previously merged PRs. A failing sync job can be retried independently of
delivery. The launcher checks for an active Octopus deployment before preparing a new
release, since a newer manifest supersedes the previous pins.

The server image and signed client downloads build in parallel in their owning
GitHub repositories. Before deploying the new image by digest, Hetzner uploads an encrypted backup
to Cloudflare R2, downloads it, and restores PostgreSQL in an isolated container.
After deployment, Octopus checks the live server revision and migration heads.
Only then are the sealed client downloads published. Web and Play internal delivery
run independently. The separate website release stays independent.

The client and server build steps retry automatically with a 15-second backoff.
If Octopus asks for failure guidance, fix the reported cause and choose **Retry**.
The release host stores GitHub run IDs under `/var/lib/papyrus-release/octopus/<version>+<build>/`.
It polls the recorded run for its expected title, branch and workflow path before
acting on the result. Retries follow the existing run or rerun its failed jobs.
For restore drills and recovery keys, see [production R2 backups](tools/backup/R2_BACKUPS.md).
The **Verify release connection** runbook checks the Octopus worker, SSH and GitHub
connection without creating a release.

The Octopus service account needs project, release and task read access in
addition to its release creation and deployment permissions. The release trigger uses GitHub OIDC restricted to the workspace release workflow on
`master`. Octopus stores a dedicated SSH credential restricted on Hetzner to the named
release stages. Signing, Play credentials, and backup credentials stay with their
existing owners. The Octopus process definition is in `tools/octopus/configure.py`.

## One-time setup for automatic merges

Install the two organization-owned Apps on the workspace, client and server
repositories. Restrict each workspace Actions environment to the `master`
branch. Set the environment variable `PAPYRUS_APP_ID` to that App's numeric ID
and its secret `PAPYRUS_APP_PRIVATE_KEY` to the PEM generated for Actions.

| Environment | App | Repository permissions requested for each token |
| --- | --- | --- |
| `release-promotion` | `papyrus-release-runner` | Contents, pull requests and Actions write. Checks and commit statuses read. |
| `release-review` | `papyrus-release-reviewer` | Contents read and pull requests write. |

Set `PAPYRUS_RELEASE_AUTHOR` in `release-review` to `papyrus-release-runner[bot]`.
The runner mints installation tokens restricted to the three repositories and
refreshes them while waiting for CI. The review job uses its own App identity.
Generate a separate author key for GitHub Actions so it can be rotated
independently of the key on Hetzner.

The reviewer workflow runs from workspace `master` and reads PRs as Git objects.
GitHub enforces branch protection and required checks on the merge request.
Keep the two credentials in their respective environments.

Land the workflows on workspace `development` and promote them to `master` before
the first run. Install the matching release host code and update the Octopus
process with `tools/octopus/configure.py` between deployments. Its prepare step
reads the workspace SHA from the release notes and records it for retries.
[Octopus release notes](https://octopus.com/docs/releases/release-notes/)

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
