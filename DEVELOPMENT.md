# Development tooling

Papyrus is a workspace of independent Git submodules. The client is Flutter with
Provider, SQLite/PowerSync, platform adapters and a Git-pinned EPUB/PDF reader.
The server is FastAPI with async SQLAlchemy, Pydantic, Alembic, PostgreSQL and a
self-hosted PowerSync service. Inspect the source for implemented capabilities;
some README feature lists and setup links are ahead of the current checkout.

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
| `tools/papyrus deps server` | Install server/dev dependencies with the uv lock |
| `tools/papyrus check client` | Non-writing Dart format check, Flutter analysis, four web-bootstrap tests |
| `tools/papyrus check server` | Ruff lint, Ruff format check, strict Mypy |
| `tools/papyrus check tooling` | Eight CLI regression tests and ShellCheck |
| `tools/papyrus check all` | All three check groups above; reports independent failures |
| `tools/papyrus test client -- test/auth/token_store_test.dart` | Focused Flutter tests; pass options after `--` |
| `tools/papyrus test client -- --coverage` | Full client suite and coverage |
| `tools/papyrus test server -- tests/services/test_sync.py` | Focused backend tests |
| `tools/papyrus test server` | Full backend suite excluding provider auth smoke tests |
| `tools/papyrus run client` | Chrome app with local Dart defines on port 3000 |
| `tools/papyrus run server` | Reloading API on port 8080 |

Use `tools/flutter` and `tools/dart` when a command is not covered by the CLI. These
wrappers fail if the pinned SDK is missing. They do not silently use global Flutter.
Reader `deps`, `check`, and `test` commands are also supported; its library lockfile
is ignored, so reader dependency setup resolves its declared constraints normally.
Website and Sphinx checks remain in their own repos.

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
nested component directory. Protocol initialization and enumeration of **25 tools**
were verified from both the root and `client/app`. These include analysis, tests,
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

## Verified baseline — 2026-10-03

| Scope | Result |
| --- | --- |
| Client lockfile | Five transitive versions normalized to the CI SDK; locked installation succeeds |
| Client formatting | 449 Dart files checked, no changes required |
| Client analysis | No issues |
| Client tests | 1,387 passed; 19 skipped |
| Web bootstrap | Four tests passed |
| Web build | Release compilation and Wasm dry run succeeded |
| Reader tests | 93 passed; 21 skipped in the sibling checkout |
| Server lint/types | Ruff lint and Mypy pass (138 source files) |
| Server tests | 340 passed; two provider smoke tests excluded |
| Server formatting | Five pre-existing files need formatting; check correctly fails |
| Tooling | Eight regression tests, ShellCheck and skill frontmatter validation pass |
| Dart MCP | Initialized successfully; 25 tools enumerated from root and nested cwd |
| Local platforms | Flutter doctor finds Android SDK, Xcode, Chrome and macOS target |
| GitHub CLI | Installed; account sign-in remains pending |

The five server formatting files are `papyrus/models/powersync_demo.py`,
`tests/api/routes/test_auth_sandbox.py`, `test_powersync_sandbox.py`,
`tests/integration/test_auth_smoke.py`, and `tests/services/test_auth.py`.
They were not reformatted as part of tooling setup. Local verification logs are
ignored under `.local/tooling/`.

Live cross-device PowerSync and external OAuth/SMTP smoke tests were not run.
Native release builds and Windows/Linux builds were not run. The global Flutter
SDK remains newer; Flutter doctor may report that PATH mismatch while project
commands and VS Code use FVM correctly.

The client CI now enforces its lockfile and checks formatting without writing.
The workspace tooling workflow runs the CLI regressions and ShellCheck on relevant
pushes and pull requests; it does not require the application submodules or services.

The server README links to auth, acquisition and PowerSync guides absent from
this checkout. Its existing `.env.example`, tests, services, and
`docs/opds-relay.md` are usable sources. The client reader dependency is pinned to
`08a5161b9d00eb73581f74ce087b9ad6c1568ca7`, while the sibling reader checkout is at a
different revision. Editing it alone does not change client behavior. For a joint
reader change, use an ignored `client/app/pubspec_overrides.yaml` with a local path
override during validation, then coordinate a deliberate revision update.
