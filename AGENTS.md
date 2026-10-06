# Papyrus workspace

Papyrus is an offline-first, cross-platform book library and reader. This repo
coordinates independent Git submodules; each component has its own history and CI.

## Find the owner

| Component | Location | Responsibility |
| --- | --- | --- |
| Client | `client/app` | Flutter UI, Provider state, local persistence, auth, PowerSync uploads, OPDS |
| Server | `server/papyrus` | FastAPI routes, async services, PostgreSQL models, auth, media, sync validation |
| Reader | `reader/lib` | EPUB/PDF reading engine and public reader API |
| Docs | `docs` | Sphinx documentation |
| Website | `website` | Public landing page |

Read `client/AGENTS.md` or `server/AGENTS.md` for component changes. Start with the
owning component and follow a boundary into another repo when the behavior needs it.
Use `rg` and targeted reads. Never run the workspace Pull/Setup tasks to refresh a
checkout while investigating: setup updates submodules to recorded revisions.
Preserve existing changes and detached submodule revisions. Before implementation,
check the relevant repo's status and choose a branch if commits are requested.

## Branches and releases

`development` is the default integration branch in the workspace and component
repositories. Branch fixes/features from it and target it with ordinary PRs.
Keep version numbers unchanged while accumulating fixes. `master` is the release
branch; promote `development` with a release PR when ready, using the existing
version bump process in `RELEASING.md`. Use a merge commit for promotion and bring
`master` back into `development` afterwards. Do not rename `master` or deploy from
`development`. Website versioning remains independent of client/server versions.

## Tools and checks

`tools/papyrus` resolves paths from its own location and runs from the correct repo.
Flutter is pinned in `.fvmrc` to the client CI version; use `tools/flutter` and
`tools/dart` rather than the machine's potentially newer SDK.

- `tools/papyrus doctor`: tools, SDK, local configuration, submodules, GitHub auth.
- `tools/papyrus deps client|reader|server|all`: locked dependency setup.
- `tools/papyrus check client|reader|server|references|tooling|all`: non-mutating quality checks.
- `tools/papyrus test client -- test/path_test.dart`: focused Flutter test.
- `tools/papyrus test server -- tests/services/test_sync.py`: focused pytest run.
- `tools/papyrus run client|server`: development processes.

Server fixtures drop and recreate test tables. The CLI checks for a separate local
test database and prevents overlapping CLI server test runs. Direct pytest runs
must also use a distinct test database and must not overlap on that database.
Most route tests need PostgreSQL even if they lack the `integration` marker.
Provider-backed auth smoke tests are excluded by the CLI; run them explicitly only
when that external provider test is requested and configured.

For Flutter runtime inspection, use the project Dart MCP server with `client/app`
as a root, or the CLI when MCP is unavailable. Consult Context7 or official
documentation for version-specific library behavior. No extra browser MCP is
needed when the session already provides browser/computer-use tools.

## Reusable workflows

Project skills live in `.agents/skills`:

- `papyrus-client`: client implementation and platform verification.
- `papyrus-server`: backend services, migrations and database tests.
- `papyrus-sync-contract`: changes crossing the Dart/HTTP/PostgreSQL/PowerSync boundary.

Custom roles in `.codex/agents` cover client work, server work and contract review.
Use them when delegation is requested. Give editing agents disjoint files. Share
contract decisions with both implementers and run database tests serially.

Follow the existing design tokens and e-ink motion preferences. Library operations
must work offline and remain isolated across guest, user, and server profiles.
Treat stored reading positions and user media as durable data. See
`DEVELOPMENT.md` for setup, checks and integration workflow.
