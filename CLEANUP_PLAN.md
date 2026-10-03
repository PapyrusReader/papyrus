# Repository cleanup plan

Status: proposed; audited 2026-10-03. This change records the plan and current
reader integration. Branch deletion, source removal, dependency removal, and
reorganization are future work, to be delivered in the separate PRs below.

## Scope and baseline

Keep core reading, offline library reliability, and account/server isolation as
the product priorities. Start with maintenance that reduces misleading guidance
and duplicate implementations; preserve user data and public contracts.

| Repository | Audited checkout | Default branch at audit |
| --- | --- | --- |
| Workspace | `7ca8bdc`, before this plan | `7ca8bdc` |
| Reader | `16611b6` (`feat/reader-core`) | `f8286e1` |
| Client | `57d670a` (`feat/reader-integration`) | `fca3438` |
| Server | `b199394` | `b199394` |
| Docs | `57048e7`, detached workspace pin | `14ab148` |
| Website | `50e7561`, detached workspace pin | `50e7561` |

The reader release is [reader PR #3](https://github.com/PapyrusReader/reader/pull/3),
followed by [client PR #29](https://github.com/PapyrusReader/client/pull/29), then
the workspace reference update. Its client dependency is immutable Git revision
`16611b646ee93ee12a2c009f0bd6e49061c72eab`. Keep these branches while their PRs
are open. The server reference advances to the already merged tooling revision;
the docs and website checkouts remain unchanged in this release.

Audit evidence: tracked files, import/export/part reachability (including Dart
conditional imports), symbol/caller searches, workflows, Git ancestry and patch
equivalence, live GitHub branch/PR inventory, and docs Pages settings. Reachability
identifies candidates, not proof of removability. No Python dead-code conclusion
or comprehensive dependency/license audit is claimed.

## Delivery order

| Order / proposed PR | Owner | Concrete outcome | Completion gate |
| --- | --- | --- | --- |
| 1. Branch inventory and retirement | Each repository | Record retained SHAs and remove verified obsolete remote heads; prune stale tracking refs afterward | Re-fetch, verify worktrees/open PRs and branch rules; use the decision table below |
| 2. Current documentation and CI ownership | Workspace, client, server, docs, website | Fix misleading setup/features/links, archive completed plans, remove client-owned server workflows | Validate links and each affected workflow; build Sphinx/site; verify no required check or release depends on retired workflows |
| 3. Unused client presentation, assets and dependencies | Client | Remove caller-verified obsolete UI in small batches, with its obsolete tests | Analyzer, focused behavior tests, full offline suite, web build; native checks for plugin changes |
| 4. Test organization and coverage review | Client, reader, server | Map retained tests to current contracts; separate opt-in service tests from fast local checks | No lost persistence/reader contract coverage; deterministic local lane and documented service lane |
| 5. Reader presentation extraction | Reader, then client pin | Split shell controls/settings/navigation without changing engine/public API behavior | Reader suite, example checks, browser regressions, host integration and platform CI |
| 6. Client and server bounded restructuring | Owning component, one feature at a time | Extract library/acquisition responsibilities and isolate production/demo data | Feature and contract tests; persistence behavior unchanged; no bundled schema changes |
| 7. Prevent recurrence | Workspace and component CI | Explicit check/dependency scope, reproducible artifacts, docs freshness and pin consistency | CLI regression checks and a clean-checkout run; documented fast/full commands agree with CI |

Prioritize 1–3 before moving directories. Each PR should name removed callers,
the replacement behavior, relevant verification, and its rollback commit. Record
before/after CI duration, tracked-file count, and dependency/build size where
affected; do not promise a performance gain from line counts alone.

## Branch decisions

These are actual GitHub heads, not merely local `origin/*` references. Refresh
this inventory immediately before executing retirement.

| Repository / branch / tip | Evidence | Proposed action |
| --- | --- | --- |
| Client `feature/124c0xc0kbv-slack-community` / `6e78c8d` | [PR #27](https://github.com/PapyrusReader/client/pull/27) merged; both commits patch-equivalent to master | Retire after checking no dependent open PR/worktree |
| Reader `chore/refresh-example-lockfile` / `b3fc338` | [PR #2](https://github.com/PapyrusReader/reader/pull/2) merged; tip is an ancestor of master | Retire |
| Server `chore/development-tooling` / `71b21c6` | [PR #6](https://github.com/PapyrusReader/server/pull/6) merged; patch-equivalent to master | Retire |
| Docs `codex/kar-5-sphinx-readme` / `fd4c61c` | [PR #1](https://github.com/PapyrusReader/docs/pull/1) merged; tip is an ancestor of master | Retire |
| Workspace `feature/opds-support` / `f540e85` | [PR #3](https://github.com/PapyrusReader/papyrus/pull/3) merged an earlier head `fb95d18`; four subsequent commits only update client/reader gitlinks | Conditional retirement: confirm every referenced component revision is preserved/covered by current component master; retain the tip in the retirement record |
| Client `copilot/fix-running-task-attempts` / `a5294b2` | [PR #10](https://github.com/PapyrusReader/client/pull/10) closed unmerged; one unique “Initial plan” commit, but its tree is identical to its parent | Retirement candidate after checking abandoned-task/dependent-PR references; distinguish it from merged work |
| Docs `gh-pages` / `069a780` | Unique publication-history commit; Pages currently uses Actions, with source master, and Deploy uploads/deploys artifacts | Hold until deployment references, rollback needs and branch rules are checked; archive publication history before deciding |
| Active reader/client/workspace release branches; all `master` branches | Current PRs/default branches | Keep |
| Website | Only master exists remotely | No branch cleanup needed |

Some local tracking refs already outlive their remote heads, including workspace
and client `chore/development-tooling` and several older client feature branches.
Use `git fetch --prune` only after recording the live inventory. Review local
branches separately: workspace tooling is merged, while local reader/docs master
branches are behind. Do not reset detached submodules or delete a checked-out
branch. `git cherry` can miss a combined squash; check PR head/merge SHAs and the
actual patch rather than treating every non-ancestor as unmerged work.

Before any remote deletion, record the full tip SHA and PR URL, check tags,
worktrees, protected/required branches, deployments and dependent PRs. Delete only
the reviewed head, never force-purge history. A preserved tip can restore the head
with `git push origin <recorded-full-sha>:refs/heads/<branch>`.

## Documentation and automation repairs

| Location | Verified problem | Planned repair |
| --- | --- | --- |
| Workspace `DEVELOPMENT.md` | Claims reader lockfile is ignored; records an old reader pin and pending GitHub sign-in | Describe committed reader lockfile/worker reproducibility; replace transient workstation status with reproducible commands. Keep the dated baseline as history and label newer results separately |
| Workspace `tools/papyrus`, `AGENTS.md`, VS Code tasks | `check all` covers tooling/client/server, not reader; reader dependency setup does not enforce its committed lockfile | Define “all” explicitly; add reader checks/locked dependency setup and an opt-in example/browser lane. Update help, tasks, docs and CLI regressions together |
| Client `README.md` | Format/feature lists exceed the EPUB/PDF reader adapter; old coverage/project links | Separate reading support from metadata import and roadmap; verify each storage/annotation/statistics claim against active code |
| Client `.github/workflows/server-ci.yml`, `server-release.yml` | Both target nonexistent `server/` inside the client repo; typecheck nonexistent `src/`; server has its own CI | Confirm no repository rules/tag-release consumers require them, then delete these two workflows. Keep Flutter CI/release workflows |
| Server `README.md` | Missing `docs/flutter-auth-integration.md`, `auth-testing.md`, `powersync-sandbox.md`, `acquisition-downloads.md` | Restore concise current runbooks or replace links with real sources. Use `.env.example`, routes, tests and dev pages; do not invent missing guide contents |
| Server `AGENTS.md` | Infrastructure map names `papyrus/core` for configuration, while config is `papyrus/config.py` | Correct ownership paths while preserving migration/test rules |
| Docs `design/server-architecture.rst`, `_static/openapi.yaml` | Manually maintained API paths/contracts; architecture uses `/api/v1`, while `.env.example` configures `/v1` | Make API prefix configurable in prose; generate public schema from `create_app()` using safe deterministic test settings; exclude private dev surfaces and verify schema freshness in CI |
| Docs `implementation/technologies.rst` | Lists Supabase and Redis; current server dependencies/Compose instead use PostgreSQL, FastAPI and PowerSync | Describe the deployed implementation and clearly distinguish proposed options |
| Client `docs/catalogs-ui-redesign.md`, `docs/superpowers/plans/` | Completed plans/verification still describe unmerged working-tree state; historical agent execution instructions mixed with current guidance | Archive the four dated OPDS/catalog/library plans with status and PR links; retain useful current decisions/runbooks. Keep regenerable local screenshot commands; do not treat ignored build images as missing source |
| Docs workspace pin | `57048e7` predates already merged docs README improvements at `14ab148` | Build/check newer docs in a separate branch, then update the workspace pin in its own PR |
| Client `.gitignore`, local override guidance | Guidance calls `app/pubspec_overrides.yaml` ignored, but `git check-ignore` finds no rule | Explicitly ignore local overrides and document locked Git-pin restoration; never publish a local-path lockfile |
| Website `README.md`, `index.html` | Old `Eoic/Papyrus` docs/releases/project links; README uses `npm install` despite committed lockfile | Check final public destinations, update links and use reproducible `npm ci`; verify public capability claims against released behavior |
| Docs CI | Deploy builds only on master/dispatch; no PR documentation build | Add PR Sphinx validation, warnings policy and link/schema checks; preserve requirement IDs and publication behavior |

Source ownership: workspace owns setup/coordination; each component README owns
its current commands and capabilities; reader `docs/integration.md` owns the
host/package contract; server schema/routes own HTTP contracts; docs owns the
published narrative; completed plans belong in dated archives with a current
index. Avoid maintaining copied setup commands and API tables in multiple places.

## Client source and file candidates

The tracked Dart graph rooted at `app/lib/main.dart` found 21 unreachable source
files. Symbol checks confirmed apparent matches such as private `_RegisterForm`
in `register_page.dart` are a different implementation. Generic `Book`, `Search`
and `SearchField` names also require import-aware review, not plain name counts.
Re-run the graph after preceding PRs; include tool/test/platform entry points.

### First removal batch: obsolete presentation

The following 14 files have no Dart importers in the audited source/tests:

```text
app/lib/forms/login_form.dart
app/lib/forms/register_form.dart
app/lib/pages/books_page.dart
app/lib/widgets/book_details/eink_book_details_tab_bar.dart
app/lib/widgets/filter/active_filter_bar.dart
app/lib/widgets/goals/add_goal_card.dart
app/lib/widgets/heading.dart
app/lib/widgets/input/text_input.dart
app/lib/widgets/profile/profile_stats_card.dart
app/lib/widgets/profile_button.dart
app/lib/widgets/search_settings.dart
app/lib/widgets/shared/eink_page_header.dart
app/lib/widgets/shared/quick_filter_chips.dart
app/lib/widgets/shared/view_mode_toggle.dart
```

`login_form.dart` is entirely commented-out code. The router uses the current
library/auth pages. `app/lib/widgets/book/book.dart` and
`app/lib/widgets/search.dart` form a further two-file chain imported only by the
obsolete `books_page.dart`; remove together if the final caller check confirms
that chain. Keep `app/lib/models/book.dart`, the active library widgets, and
current auth forms inside their pages.

### Second removal batch: test-only implementations

| Candidate | Existing test consumers | Required review |
| --- | --- | --- |
| `app/lib/models/active_filter.dart` | `test/models/active_filter_test.dart`, plus the obsolete active filter bar | Compare against current `LibraryFilters` and retained serialized data |
| `app/lib/models/search_filter.dart`, `app/lib/utils/search_query_parser.dart` | `test/models/search_filter_test.dart`, `test/utils/search_query_parser_test.dart` | Check whether advanced-query behavior is a product requirement; preserve useful semantics in active search before retiring implementation/tests |
| `app/lib/widgets/add_book/digital_book_import_sheet.dart` | `test/widgets/add_book/digital_book_import_sheet_test.dart` | Compare selection, cancellation and file-picker contracts with active `BookImportSheet` |
| `app/lib/widgets/add_book/book_import_results_sheet.dart` | `test/widgets/add_book/book_import_results_sheet_test.dart` | Preserve retry, commit, duplicate handling and temporary-file cleanup coverage in active import flow |

Delete a test only when its sole implementation is retired and its useful
behavior is covered by the replacement. Do not keep dead UI solely to keep its
tests green, or remove behavior tests solely to reduce suite size.

### Assets, dependencies and generated files

- `app/assets/images/auth-illustration-25.png` and `auth-illustration-3.png` have
  no tracked text references and are absent from the asset manifest: first asset
  removal candidates. Check native references and documentation previews first.
- `book_placeholder_2.jpg` and `profile.png` are declared but have no other
  filename references. Investigate dynamic paths before removing declarations
  and files. `book_placeholder.jpg` is used by a layout test; review separately.
- `collection`, `cupertino_icons` and `google_fonts` have no direct package URI
  imports in client `app/lib`; inspect tests, generated code, fonts, tooling and
  platform registration before proposing dependency removal. Resolve lockfiles
  and measure build impact in that PR.
- Keep `epub_pro`, `syncfusion_flutter_pdf`, `dart_mobi` and `unrar_file` for now:
  `file_metadata_service.dart` actively imports them. Reader support for EPUB/PDF
  does not mean broader metadata-import dependencies are unused.
- Keep committed lockfiles, reader `assets/epub_worker.js`, its generator and
  third-party notices, theme-generated source, native runner files and Podfiles.
  Generated-but-packaged files need a reproducible generator, not blanket deletion.
- Build/cache directories, `.local/` screenshots, local media/databases and
  credentials are separate from tracked source. Inventory disk usage separately;
  do not use `git clean -xfd`, the workspace Purge task, or database resets as
  housekeeping shortcuts. No local user-data purge is part of this plan.

## Test retention and execution lanes

Retain regressions for profile isolation, offline upload queues/tombstones,
media retry/cleanup, schema migrations, auth ownership, EPUB chapter order and
reflow offsets, PDF modes/refitting, theme popup contrast, focus-mode keyboard
navigation, session identity and version-1 locator restoration. Legacy CFI and
old metadata envelopes are compatibility data; old sync-route rejection tests
also protect the current API. “Legacy” is not a removal criterion.

Current optional tests are intentional: client OPDS network smoke tests require
`OPDS_SMOKE_URL`; live sync requires `PAPYRUS_LIVE_SYNC`/integration configuration;
server auth smoke tests require provider/SMTP configuration; local env-file
tests can skip when `.env` is absent. Document conditions and run these in a
separate configured lane rather than deleting them as stale tests.

For each feature extraction, list covered contracts and duplicate assertions.
Keep a fast deterministic unit/widget lane, native/browser reader transport and
layout lanes, and an explicitly configured network/provider/device lane. Run
server database tests serially against a distinct test database: shared fixtures
drop/recreate tables. This audit did not run destructive database tests.

## Bounded structure improvements

Keep the independent repositories and submodule coordination initially. A
monorepo conversion would change release/CI ownership and is not justified by
this audit. Improve reproducible pins and check orchestration first.

1. **Reader:** `lib/src/presentation/papyrus_reader.dart` is 1,872 lines. Extract
   private settings/contents panels, toolbar/progress controls and command/focus
   coordination within presentation. Keep `lib/papyrus_reader.dart` exports,
   controller/engine interfaces and stable viewport keys. Preserve native/web
   worker boundaries and theme ownership. The reader public-export/worker graph
   has no orphan runtime files; exported `EpubPaginator` and custom renderers are
   API, not automatic deletion candidates. Re-run chapter-boundary arrow keys,
   control visibility/resume, opposite host/reader themes, PDF layouts and phone
   resizing after each extraction.
2. **Client:** start with the library page (1,066 lines), advanced filter sheet
   (1,059), and acquisition downloads provider (1,058). Extract responsibilities
   into their existing feature directories before changing imports broadly.
   Then pilot `lib/features/<feature>/{presentation,application}` for one feature
   if it makes ownership clearer; keep shared persistence, PowerSync, media,
   auth/platform contracts stable. Align tests with final feature boundaries.
   Avoid moving all `pages/widgets/providers` in one mechanical PR.
3. **Demo data:** `data/sample_data.dart` is 949 lines and still called as a
   fallback by `BookDetailsProvider.loadBook`. It is live code. Plan explicit
   demo/test injection and a product decision for missing-book behavior before
   moving samples out of production; add a focused missing-book regression.
4. **Server:** `services/acquisition.py` is 1,310 lines; monitor 562, acquisition
   routes 413. Follow the existing auth service package pattern: propose
   `services/acquisition/` with provider adapters, job orchestration and shared
   types, retaining a stable facade. Keep routes thin and transaction/owner
   boundaries explicit. Preserve qBittorrent compatibility, retry/cancellation,
   sync atomicity and post-commit media deletion. Keep schema/migration changes
   in separate behavior PRs, not bundled into moves.

## Validation and prevention

The accepted reader snapshot passed 140 reader tests, example tests/analysis/web
build, and Chrome worker/layout/focus/theme/resume checks. The client passed 23
focused reader tests, formatting/analysis, four bootstrap tests and web release
build against the published Git dependency. Reader platform CI and client CI
also passed for the release heads. These are the release baseline, not proof
that proposed cleanup has been implemented or validated on physical devices.

During execution, use the pinned workspace SDK and owning component checks.
Reader worker edits require regeneration and drift checks; platform/plugin edits
require the affected native and web targets; shared client composition changes
require the full offline suite. Docs changes require a Sphinx build and internal
links; website changes require its locked build and visible-link review.

Add prevention only for observed drift: dead-link checks, safe OpenAPI freshness,
reader asset reproducibility, an import-graph report with explicit public/tool
entry points, and Git dependency/workspace-pin consistency. Root tooling CI
currently does not trigger on gitlink-only updates; add a lightweight consistency
check that accepts intentional release commits and states the merge order.
Do not add blanket unused-code rules that mistake platform implementations or
public APIs for dead code.

Completion means reviewed obsolete heads are retired, current documentation
matches source, selected dead implementations and their redundant tests are
removed without losing contracts, each extraction has clear ownership and green
checks, and follow-up candidates have explicit decisions. Retain deferred items
in this plan with reasons rather than silently expanding the cleanup scope.
