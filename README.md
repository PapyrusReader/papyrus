# Papyrus workspace

This repository is the development entry point for Papyrus. It pins the client,
server, reader, website, and documentation repositories as Git submodules so a
team can reproduce a known working combination of projects.

## Prerequisites

- Git
- Flutter
- Docker with Docker Compose
- [uv](https://docs.astral.sh/uv/)
- OpenSSL
- VS Code (recommended for the included tasks)

## Clone

The GitHub repository has not been published yet. Once it is available, clone it
with its submodules:

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

The setup is safe to rerun. It does not overwrite existing configuration or
delete development data.

From a terminal, run the same setup with:

```bash
./.vscode/setup.sh
```

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
uv run uvicorn papyrus.main:app --reload --host 0.0.0.0 --port 8080
```

```bash
cd client/app
flutter run -d chrome \
  --web-hostname papyrus.localhost \
  --web-port 3000 \
  --dart-define-from-file=.dart_defines
```

The `reader`, `website`, and `docs` directories are included for focused work,
but they are not started as part of the core application workflow.

## Update or reset the workspace

The VS Code `Pull` task pulls the parent repository when its remote is
configured, then synchronizes every submodule to the revision pinned by the
parent. Its terminal equivalent is:

```bash
./.vscode/pull.sh
```

The `Purge` task deletes local Papyrus databases, uploaded media, PowerSync
state, and browser storage before recreating clean service state. It requires
typing `RESET` exactly. Stop the Client and Back-end tasks before running it.

## Advance a project revision

Submodule versions change intentionally. A maintainer updates the desired child
repository, validates the complete workspace, and commits the resulting gitlink
in this repository. For example:

```bash
git -C client pull --ff-only origin master
git add client
git commit -m "chore: update client"
```

Other developers receive that exact revision the next time they run `Pull`.
