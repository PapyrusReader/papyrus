---
name: papyrus-server
description: Implement or debug Papyrus FastAPI services, PostgreSQL persistence, auth, media, and Alembic migrations in server.
---

Read `server/AGENTS.md` from the Papyrus workspace and work from `server/`. Locate
the workspace by ascending to `.fvmrc` and `tools/papyrus`. Use locked `uv` tools;
`tools/papyrus deps server` installs existing dev dependencies without upgrading.

Trace an endpoint from `papyrus/api/routes` through `papyrus/services` into schemas
and SQLAlchemy models. Keep domain rules and transaction orchestration in services.
New router modules need registration in `papyrus/api/routes/__init__.py`; new models
need exports through `papyrus.models` for Alembic discovery.

For ownership-sensitive changes, trace the authenticated user through every query,
referenced entity and media path. For sync changes, preserve atomic batches,
owner-level serialization, deletion tombstones and physical deletion after commit.
Inspect `papyrus/services/library_sync.py` and `sync.py` for the existing semantics.

For persisted schema changes, update models and add/review an Alembic revision.
Inspect both the existing database migration tests and affected domain tests.
Autogeneration needs a running database at the expected current revision; a generated
file alone does not verify an upgrade or data preservation. Apply migrations to a
disposable database when validation needs it. Honor existing approval requirements
for destructive production migrations.

Use `tools/papyrus test server -- tests/<focused_path>.py`. The test fixtures create
roles/databases and drop/recreate tables. They need local PostgreSQL even without an
`integration` marker. Do not run overlapping suites on the same test database.
CLI runs exclude externally backed `auth_smoke` tests; provider smoke testing is a
separate opt-in workflow. Do not print environment files or rotated auth tokens.

Run `tools/papyrus check server` for Ruff lint, non-mutating formatting and the
repo's configured strict Mypy. Mypy is installed and used by server CI; adding a
different type checker is not required. If sandbox TS changes, install with
`npm --prefix frontend/dev-pages ci`, then run its `typecheck` and `build` scripts.

For auth, upload or PowerSync wire changes, also read the workspace
`papyrus-sync-contract` skill and inspect the corresponding client tests.
