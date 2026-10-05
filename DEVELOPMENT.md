# Development tooling

Papyrus is a workspace of independent Git submodules. The client is Flutter with
Provider, SQLite/PowerSync, platform adapters and a Git-pinned EPUB/PDF reader.
The server is FastAPI with async SQLAlchemy, Pydantic, Alembic, PostgreSQL and a
self-hosted PowerSync service. Product requirements describe planned capabilities; component READMEs and source
describe current behavior.

## Setup

The workspace uses Flutter **3.41.2 / Dart 3.11.0**, matching client CI and release
workflows. FVM installs that SDK separately from the machine's global Flutter.
Keep `.fvmrc` and the workflow version pins aligned when upgrading deliberately.

```bash
dart pub global activate fvm
tools/papyrus sdk
tools/papyrus deps all
tools/papyrus doctor
```

Put `~/.pub-cache/bin` on PATH for the `fvm` command. The workspace CLI also finds
FVM there if it is not on PATH. If local config is missing, follow the root README
setup workflow; dependency commands do not create config or start services.

Useful optional CLIs on macOS:

```bash
brew install gh jq shellcheck
gh auth login --web
```

GitHub sign-in is interactive and enables `gh` issue/PR/CI operations. No token is
stored in this repository. Use `gh pr view`, `gh run list`, and `gh run view --log-failed`
from the component repo. `jq` inspects JSON responses; ShellCheck checks shell
wrappers. Existing `uv`, Docker Compose and Codex provide the other core tools.

VS Code recommendations and settings configure Dart/Flutter, Python and Ruff,
the FVM SDK, `server/.venv`, and submodule discovery. Format on save is enabled for
Dart/Python. The installed `code` CLI can open this workspace with `code .`.

## Everyday commands

Run these from the workspace root, or invoke the CLI by its path from another
directory. All subprocesses use the appropriate component directory.

| Command | Purpose |
| --- | --- |
| `tools/papyrus doctor` | Inspect tools, config, SDK, repos, Docker and GitHub authentication |
| `tools/papyrus sdk` | Install/link the FVM version from `.fvmrc` |
| `tools/papyrus deps client` | Install client dependencies with its committed lock |
| `tools/papyrus deps reader` | Install reader dependencies with its committed lock |
| `tools/papyrus deps server` | Install server/dev dependencies with the uv lock |
| `tools/papyrus check client` | Non-writing Dart format check, Flutter analysis, four web-bootstrap tests |
| `tools/papyrus check server` | Ruff lint, Ruff format check, strict Mypy |
| `tools/papyrus check reader` | Formatting, worker asset drift, library and example analysis |
| `tools/papyrus check references` | Match committed workspace reader and client dependency revisions |
| `tools/papyrus check tooling` | CLI regression tests and ShellCheck |
| `tools/papyrus check all` | Tooling, reference consistency, client, reader and server; reports independent failures |
| `tools/papyrus test client -- test/auth/token_store_test.dart` | Focused Flutter tests; pass options after `--` |
| `tools/papyrus test client -- --coverage` | Full client suite and coverage |
| `tools/papyrus test server -- tests/services/test_sync.py` | Focused backend tests |
| `tools/papyrus test server` | Full backend suite excluding provider auth smoke tests |
| `tools/papyrus run client` | Chrome app with local Dart defines on port 3000 |
| `tools/papyrus run server` | Reloading API on port 8080 |

Use `tools/flutter` and `tools/dart` when a command is not covered by the CLI. These
wrappers fail if the pinned SDK is missing. They do not silently use global Flutter.
Reader `deps`, `check`, and `test` commands also use the pinned SDK and committed
lockfile. Run the reader browser suite after UI, layout, focus, or worker changes
as described in `reader/docs/validation.md`.

Website checks use `npm ci && npm run build` in `website/`. Documentation checks
use `uv sync --locked --extra dev && make build` in `docs/` (Graphviz required).

Production on Hetzner uses three independent Compose projects: the shared HTTPS
entry point in `deploy/edge`, app services in `server/deploy`, and the public
website in `website/deploy`. Only the entry point owns public ports 80/443;
the other projects expose internal services through the `papyrus-edge` network.
Run `python3 tools/check_deployments.py` to validate project separation, routing
aliases, published ports and private database networks without starting services.
Each project's runbook covers its own deployment and releases.

For a joint reader/client change, validate a local reader using an ignored
`client/app/pubspec_overrides.yaml`, then remove the override and pin the published
reader commit in `client/app/pubspec.yaml`. Regenerate the client lock and update
the workspace's reader and client gitlinks together. The reference check compares
committed gitlinks and committed dependency files, independently of local overrides.

Server pytest fixtures drop and recreate test tables. The CLI checks for a separate
local database named `*_test` or `test_*` and locks overlapping CLI server test runs.
Direct pytest runs must not overlap on that database. If a killed process leaves
`.local/server-test.lock`, remove it only after confirming no server test is active.
Most endpoint tests need PostgreSQL even without an `integration` marker. The
Storage task or `docker compose up -d database` from `server/` provides it.

For TS sandbox changes, use the existing lock and scripts:

```bash
npm --prefix server/frontend/dev-pages ci
npm --prefix server/frontend/dev-pages run typecheck
npm --prefix server/frontend/dev-pages run build
```

## Skills and agents

`AGENTS.md` maps ownership and tools. `client/AGENTS.md` covers Flutter conventions,
while `server/AGENTS.md` retains its service-layer, testing and migration rules.

Repo skills are in `.agents/skills` and can be selected automatically or invoked
explicitly as `$papyrus-client`, `$papyrus-server`, and `$papyrus-sync-contract`.
The contract skill includes paths for the Dart/HTTP/database/replication round trip.
Launch Codex from this workspace root to discover the workspace skills and roles.

Project roles in `.codex/agents` are `papyrus_client`, `papyrus_server`, and
`papyrus_contract_reviewer`. They inherit the chosen model and reasoning. The
reviewer is configured read-only. The project caps delegated agents at two;
ordinary work remains a single-agent workflow unless delegation is requested.

Example: “Use the client and server agents to implement this agreed payload change
in disjoint files, then use the contract reviewer. Run database tests serially.”

## Dart MCP and documentation

`.codex/config.toml` starts the official Dart tooling MCP through `tools/dart-mcp`,
which uses the pinned SDK. The launcher finds the workspace from the root or a
nested component directory. It provides analysis, tests,
hot reload, widget inspection and runtime-error inspection.

Restart the Codex session to load new project MCP/agent configuration if needed.
Use `client/app` as the MCP project root. Use CLI checks if the active session
does not expose the new server yet. Runtime tools need a running instrumented app.
Context7 is already configured for framework/library documentation; existing
computer-use tools cover browser inspection when available.

The layout follows [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
and [Dart MCP setup](https://docs.flutter.dev/ai/get-started). FVM's
[project configuration](https://fvm.app/documentation/getting-started/configuration)
keeps SDK selection separate from the global toolchain.

## Coordinated releases

See [RELEASING.md](RELEASING.md) for shared client/server version metadata,
Android build numbers, first Google Play internal testing and the production
server deployment runbook. `python3 tools/release.py check` validates the local
release snapshot; CI uses `--committed` to check the recorded submodule revisions.
