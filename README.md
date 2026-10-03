# Papyrus

This repository is the development entry point for Papyrus.

## Prerequisites

- [Flutter](https://flutter.dev/)
- [Docker](https://docs.docker.com/)
- [Python](https://www.python.org/) and [uv](https://docs.astral.sh/uv/) package manager
- [FVM](https://fvm.app/) to use the project Flutter version (`dart pub global activate fvm`)
- [VS Code](https://code.visualstudio.com/) is recommended to run the included tasks

## Clone

Clone the repository with its submodules:

```bash
git clone --recurse-submodules https://github.com/PapyrusReader/papyrus.git
cd papyrus
```

If the repository was cloned without `--recurse-submodules`, initialize the
projects afterward:

```bash
git submodule update --init --recursive
```

## Set up the workspace

In VS Code, run the `Setup workspace` task once. It installs the client and
server dependencies, creates missing local configuration from the supplied
examples, generates development PowerSync keys, starts the prerequisite
containers, applies database migrations, and configures PowerSync replication.
You can also run the setup script directly in a terminal as `./.vscode/setup.sh`.

## Run Papyrus

Run these VS Code tasks independently, in order:

1. `Storage`
2. `Back-end`
3. `Client`

The services are then available at:

- Client: <http://papyrus.localhost:3000>
- API: <http://localhost:8080>
- PowerSync: <http://localhost:8081>
- Mailpit: <http://localhost:8025>

The equivalent terminal commands are:

```bash
cd server
docker compose up database mailpit powersync-storage powersync
```

```bash
cd server
uv run --locked uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
```

```bash
cd client/app
../../tools/flutter run -d chrome \
  --web-hostname papyrus.localhost \
  --web-port 3000 \
  --dart-define-from-file=.dart_defines
```

## Reset the workspace

The `Purge` task deletes local Papyrus databases, uploaded media, PowerSync
state, and browser storage before recreating clean service state.

## Development tools

Use the workspace CLI from the repository root:

```bash
tools/papyrus doctor
tools/papyrus deps all
tools/papyrus check all
tools/papyrus test client -- test/auth/token_store_test.dart
tools/papyrus test server -- tests/services/test_sync.py
```

It uses the Flutter version pinned in `.fvmrc` and the server's uv lockfile.
The same checks and test suites are available as VS Code tasks.

See [development tooling](DEVELOPMENT.md) for the project skills, agent roles,
Dart MCP integration, and setup instructions.
