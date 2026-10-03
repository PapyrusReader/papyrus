---
name: papyrus-client
description: Implement or debug Papyrus Flutter UI, reader integration, local storage, and platform behavior in client/app.
---

Read `client/AGENTS.md` from the Papyrus workspace. Work in `client/app`; use the
workspace's `tools/flutter`, `tools/dart`, and `tools/papyrus` for the pinned SDK.
Locate the workspace by ascending from the active directory to `.fvmrc` and `tools/papyrus`.

Trace a feature from its page/widget through the Provider state to its repository
or service. `lib/main.dart` owns composition; `lib/config/app_router.dart` owns
navigation. Inspect existing tests in the corresponding `test/` domain before
choosing a regression. For purely visual edits, inspect shared theme tokens and
responsive widgets rather than adding a second style system.

For persisted changes, follow writes and subscriptions in `lib/data` and
`lib/powersync`. A successful UI update is not evidence that an offline edit was
saved. Account for guest/user/server profile switches and scoped media caches.
For e-ink UI, inspect `lib/themes/app_motion.dart` and e-ink tests. For platform
changes, preserve conditional imports and validate the relevant target.

For reading changes, inspect `lib/reader` and the actual `papyrus_reader` revision
in `pubspec.yaml`/`pubspec.lock`. The sibling reader checkout is independent. EPUB
and PDF locators have different semantics; preserve restart/resume and progress.

Use Dart MCP with `client/app` as a root when available for diagnostics, tests and
running-app inspection. CLI checks remain available when MCP is not attached.
Consult Context7 or official docs for framework APIs specific to the installed SDK.

Run focused tests and `tools/papyrus check client`; for platform adapter changes,
also compile web or the affected available native target. Run the full suite when
the change spans shared composition/persistence. Distinguish analyzer warnings,
formatting debt, actual failures, and unrun targets. The OPDS network smoke test
needs `OPDS_SMOKE_URL`; it is not part of an ordinary offline test run.

If wire payloads, auth flows, or sync fields change, also read the workspace
`papyrus-sync-contract` skill. Do not broaden a client-only task into a backend
refactor unless the behavior requires it.
