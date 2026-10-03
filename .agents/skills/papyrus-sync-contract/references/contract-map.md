# Contract entry points

Paths below are relative to the Papyrus workspace. Verify the current source before
changing a contract; this map identifies owners rather than freezing field lists.

| Contract | Client | Server |
| --- | --- | --- |
| API base/prefix | `client/app/lib/auth/papyrus_api_config.dart` | `server/papyrus/config.py`, `server/papyrus/main.py` |
| Auth/token lifecycle | `client/app/lib/auth/{auth_api_client,auth_repository,token_store}.dart`, `lib/providers/auth_provider.dart` | `server/papyrus/api/routes/auth.py`, `schemas/auth.py`, `services/auth/` |
| PowerSync credentials and upload queue | `client/app/lib/powersync/papyrus_powersync_connector.dart` | `server/papyrus/api/routes/sync.py`, `schemas/sync.py`, `services/sync.py` |
| Library schema and row mapping | `client/app/lib/powersync/{papyrus_schema,powersync_book_mapper,library_row_mapper,library_database}.dart` | `server/papyrus/models/sync.py`, `schemas/book.py`, `services/library_validation.py`, `services/library_sync.py` |
| Replication projection | `client/app/lib/powersync/papyrus_schema.dart` | `server/powersync/sync-config.yaml`, `scripts/setup_local_powersync.sh` |
| Profiles and repository watches | `client/app/lib/powersync/{powersync_service,sync_profile_switch_queue}.dart`, `lib/data/data_store.dart` | JWT user identity and row ownership predicates |
| Media upload/cache | `client/app/lib/media/`, `lib/services/book_download_service*` | `server/papyrus/api/routes/media.py`, `services/media.py`, `schemas/media.py` |
| OPDS relay | `client/app/lib/opds/opds_http_client.dart` | `server/papyrus/api/routes/opds.py`, `services/opds.py`, `schemas/opds.py`, `docs/opds-relay.md` |
| Managed acquisition | `client/app/lib/acquisition/`, `lib/providers/acquisition_downloads_provider.dart` | `server/papyrus/api/routes/acquisition.py`, `services/acquisition.py`, `services/acquisition_monitor.py` |

## Useful existing regressions

- Client sync: `client/app/test/powersync/`, especially mapper, persistence, connector,
  live-library, schema-mode and profile-switch tests.
- Client auth: `client/app/test/auth/`, `test/providers/auth_provider_test.dart`.
- Client media isolation: `client/app/test/media/media_profile_switch_contract_test.dart`,
  `media_storage_scope_test.dart`, and upload queue tests.
- Server batches/ownership/deletion: `server/tests/api/routes/test_sync.py`,
  `test_library_sync.py`, `test_bookmark_sync.py`, `tests/services/test_sync.py`.
- Server schema/projection: `server/tests/test_powersync_sync_config.py`,
  `test_library_migration.py`, `test_bookmark_migration.py`.
- Server auth: `server/tests/api/routes/test_auth.py`, `tests/services/test_auth.py`.
- End-to-end: `client/app/integration_test/powersync_books_integration_test.dart`
  is gated by `RUN_POWERSYNC_INTEGRATION=true`. It registers temporary users and
  validates two clients, offline reconnect, deletion and isolation against live
  services. It imports `dart:io`; do not describe it as a browser integration test.

Example focused checks, from the workspace:

```bash
tools/papyrus test client -- test/powersync/papyrus_powersync_connector_test.dart
tools/papyrus test server -- tests/api/routes/test_sync.py tests/services/test_sync.py
```

For a deliberately requested live run on an available native device:

```bash
tools/papyrus test client -- integration_test/powersync_books_integration_test.dart \
  -d macos --dart-define=RUN_POWERSYNC_INTEGRATION=true \
  --dart-define-from-file=.dart_defines
```

Check `tools/flutter devices` first. Configure server auth/email requirements for
the disposable test users before running. Never equate the test's default skip
with a successful live sync run.
