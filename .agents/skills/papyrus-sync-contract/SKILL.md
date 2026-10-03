---
name: papyrus-sync-contract
description: Coordinate or review Papyrus client/server changes to auth, PowerSync schemas, offline uploads, ownership, and media contracts.
---

Use for changes that cross the client/server boundary. Find the Papyrus workspace
by ascending to `.fvmrc` and `tools/papyrus`; read the affected component AGENTS.md.
Read [contract map](references/contract-map.md) for exact code and test locations.
Check only the contract involved in the request.

For a synced field, follow the full round trip: Dart model and mapper -> SQLite
column -> queued CRUD serialization -> server Pydantic validation -> owned SQLAlchemy
row -> PowerSync SELECT/alias -> client decoding and repository subscription.
Check ID aliases, omitted versus null fields, booleans, UTC timestamps, JSON text,
foreign-key ownership and local-only guest behavior. Update all required layers
together; a route test alone cannot prove the replicated field round trip.

Preserve upload transaction acknowledgment after successful server persistence.
Failed batches must remain retryable. Server batches serialize per owner and commit
atomically; deleting physical media occurs after commit. Tombstones stop delayed
offline writes from reviving deleted entities. Tests should prove the affected
failure mode, including account/server profile isolation when relevant.

For auth, trace refresh, token expiry, logout and profile transition as well as the
successful sign-in. Distinguish opaque refresh tokens, short-lived API access JWTs
and PowerSync credentials. Keep browser callbacks/deep links and API prefixes
compatible with the client's configured server URI.

Choose focused client and server regressions from the reference map. Run the relevant
component checks. A live sync test needs the API, PowerSync, publication/replication,
keys and a supported native test target; skipped live tests must be reported as
unrun. Do not modify user libraries or purge local state to make a test pass.

For coordinated implementation, settle payload/schema decisions before assigning
disjoint client/server edits. If delegation is requested, use client/server roles
and the contract reviewer. Serialize database tests on each shared test database.
Report the contract change, tests on both sides, migration needs and unverified
round-trip behavior.
