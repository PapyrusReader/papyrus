# Goals integration

The coordinated `feat/goals-redesign` branches target `development` in reader,
server, client, and this workspace. No component version changes are included.

The reader exposes generic observations; the client owns durable foreground
tracking, goal projections, and the adaptive Goals interface; the server validates
owned immutable activity and synchronizes rule/period history. Detailed contracts
and local validation commands live in:

- `reader/docs/integration.md`
- `client/docs/goals-tracking.md`
- `server/docs-tracking.md`

Merge the reader change before the client uses its committed revision. Merge the
server alongside the client, and deploy its migration/publication/PowerSync streams
and advertised tracking capability before the integrated client release. Merge
workspace pins after their component PRs. Use the established development-to-master
release workflow when ready; this work does not trigger a version-based release.

## Coordinated changes

- [Reader #5](https://github.com/PapyrusReader/reader/pull/5): generic activity observations, committed revision `bc617deeaf2ae684f20c1000ed7cd52f9a61d0a7`.
- [Server #12](https://github.com/PapyrusReader/server/pull/12): owned ledger and tracking capability, committed revision `66a08012390d91dca05ad8d4d48470c7c999eff4`.
- [Client #40](https://github.com/PapyrusReader/client/pull/40): Goals and durable foreground tracking, committed revision `5e67e5969a956f4f42c5c330cbc38350f5ac8fd8`.

## Validation evidence

Final client integration: 1,541 tests passed, 19 skipped; final focused identity,
tracker, and persistence checks: 11 passed. Final goal replacement, deadline, UI,
projection, and persistence regressions: 41 passed. Analysis, formatting, bootstrap tests,
and production web build passed. Android and Chromium host checks exercised real
EPUB/PDF reading, durable checkpoints, exposed coverage, and pause/exit behavior.
Light/dark/e-ink Goals and fixed-footers were inspected at phone, tablet, desktop,
and enlarged text sizes. Screenshots are in the client tracking documentation.

Reader package: 145 tests passed; package/example checks, web build, browser
smoke checks, and worker drift passed. Server: 39 focused route/service/migration
checks and the final 12 aggregation/ownership regressions passed; Ruff, formatting,
and Mypy passed. Migration tests preserve pre-existing data.

Shared aggregation fixtures cover concurrent-device overlap and coverage. Live
two-device PowerSync transport was not exercised; this remains an opt-in check
with configured services, rather than a claim implied by local database tests.

## Goals refinement validation (2026-10-08)

The latest client adds progress-based Overview groups, compact completed filters,
swipeable tabs, shared collection-page toolbar sizing, improved goal/activity
sheets, Daily defaults, and multiple selected books. Activity row and history
expansion state has separate storage keys from scroll offsets. Goal details show
eligible time after creation cutoffs and overlapping intervals.

Tracking schema version 2 adds `book_ids` and validates ownership of each selected
book. Unsupported version-2 tracking stays staged without blocking older-server
library uploads; existing single-book goals retain version-1 payloads.

The final client suite passed 1,598 tests with 19 skipped. The latest 27 focused
server tracking, goal, and sync tests passed. Client formatting, analyzer and web
bootstrap checks, server Ruff/Mypy checks, and reader reference checks passed.
The production web build also passed.
Current UI review screenshots are committed in
`client/docs/images/goals-review` and embedded in client PR #40. These use isolated
synthetic activity fixtures; earlier real-reader validation is described above.
Live multi-device PowerSync transport has not been rerun.
