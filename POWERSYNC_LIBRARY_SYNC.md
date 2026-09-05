# PowerSync library integration

Implemented on `feature/powersync-library-sync` in this workspace and its `client`
and `server` submodules.

Books, shelves, topics (tags), notes, annotations, and book memberships now use
persistent repositories backed by PowerSync. UI saves await local transactions;
reactive library views follow downloaded changes. Guest storage remains local,
and account/server changes invalidate previous repository handles. Existing media
transfer behavior remains in place; device paths and temporary covers are not
uploaded as library metadata.

The server validates owned references, applies mixed-table uploads atomically,
and merges supplied fields in server acceptance order. Durable entity deletion
records prevent stale offline writes from restoring deleted data. Book deletion
removes dependent records and follows the existing media cleanup path. Shelf
deletion removes memberships and moves immediate child shelves to the root.

Migration `dcd3b384e6a4` adds the new tables and independently editable book fields.
It preserves the legacy metadata envelope. Client schema expansion preserves
existing databases and queued uploads; legacy normalization retains explicit nulls
and leaves unsupported historical values in their original envelope.

## Verification

- Full backend suite: **282 passed, 2 skipped**.
- Full Flutter suite: **1,057 passed, 10 skipped**. Subsequent shelf-icon and
  migration regression checks: **12 passed**.
- Ruff checks and changed-file formatting passed; mypy passed for the changed
  server implementation and migration.
- Flutter analysis: **no issues**. Release web build passed with icon tree shaking.
- Additive migration upgrade/downgrade and metadata backfill preservation were
  verified against a disposable database.
- Live integration passed using two independent PowerSync clients on one
  disposable account and another client on a separate account. It covered all
  synchronized domains, offline edits and queued writes across restart,
  independent-field merging, same-field server-order resolution, null clearing,
  membership removal, and deletion against stale offline writes. Disposable
  library data was removed and the test accounts disabled afterward.
- The local migration, replication grants/publication, and expanded PowerSync
  configuration were applied. Replication checkpoints advanced after restart;
  no configuration or replication errors appeared in the checked logs.

The live test exercises actual native PowerSync databases, HTTP uploads, and
downloads against the local stack. The browser client was release-built; a manual
browser UI session and a live Google OAuth consent flow were not automated.

## Rollout

See [the server sync runbook](server/docs/powersync-sandbox.md) for commands and
validation. Deploy the migration and server upload support first, refresh the
publication, activate the expanded PowerSync configuration, verify a checkpoint,
then update/restart clients. No key regeneration, dependency upgrades, or database
reset is required. The client now directly declares `sqlite_async`, already
present at the same locked version as a transitive dependency.

Standalone series, bookmarks, reading sessions/goals, guest import, conflict UI,
and expanded media transfer remain outside this change. Previously memory-only
non-book data is not automatically assigned to an account.
