# Papyrus coding style

These rules apply to new and changed client and server code. They come from the
maintainer's corrected code samples reviewed in October 2026. Apply them to the
code being changed; do not reformat or refactor unrelated files to enforce them.
Component instructions still define architecture, ownership, and validation.

## Shared rules

### Implement the current contract

Papyrus is pre-release. Do not add backwards-compatibility aliases, legacy data
fallbacks, old payload promotion, or version-specific behavior for previous app
versions. Update producers, consumers, and tests together when a contract changes.
Keep validation for malformed data, offline retry behavior, and support for current
external file formats and dependency APIs. Database schema revisions remain the
mechanism for creating and evolving the database; do not rewrite applied migration
history or reset local libraries as part of routine code cleanup.

### Put blank lines around blocks

Keep consecutive single-line statements together. A change in purpose, such as
moving from a query to result construction or from an assignment to persistence,
does not by itself require a blank line.

Put one blank line before and after blocks and multiline statements when adjacent
code exists. This includes multiline
assignments, calls, literals, loops, conditionals, and exception handling. Keep
the branches of one conditional and the clauses of one exception handler together.

Do not add padding at the start or end of a block. The rule is based
on the code's structure, not an inferred division into logical phases.
Retain language and formatter conventions for imports and declaration separation.

### Choose names that explain the role

Use meaningful callback and temporary-variable names. Prefer `left` and `right`
for comparator operands, `error` for a caught error, and `document`, `activity`,
or another appropriate noun for a collection item. Avoid single-letter names
when the reader must infer what they represent. Short conventional loop indices
remain appropriate when they are only indices.

Avoid qualifiers that do not distinguish anything useful. Use `baseUrl` instead
of `rawBaseUrl` when there is no relevant distinction between raw and processed
values. Keep qualifiers when they communicate a real difference.

Name predicates for the condition they check. When extracting a predicate,
preserve its polarity, null handling, and caller behavior.

### Make branching readable

Use guard clauses and clear branches. Replace nested ternary expressions with
an explicit conditional or a switch when several states or outcomes are involved.
A simple two-way ternary remains appropriate.

Extract a substantial validation condition into a named helper when that makes
the calling operation easier to understand. Avoid helpers that merely obscure
a simple expression or make the reader jump between functions unnecessarily.

### Give repeated or distracting details a name

Use named constants for defaults, masks, and protocol values when their meaning
would otherwise be hidden in a literal. Use focused helpers for URL construction
and similar details that distract from the operation using them.

Keep helpers and constants in a scope where all their callers can access them.
Preserve explicit types and immutability where appropriate. Extraction must not
change behavior, visibility, or public contracts as an incidental style change.

### Keep comments useful

Remove comments and documentation that merely repeat a declaration or narrate
an obvious operation. Avoid decorative section banners. Use names and structure
to explain straightforward code.

Retain useful explanations of constraints, non-obvious behavior, and contracts.
Existing server instructions require these explanations to use docstrings rather
than inline comments. The client examples permit substantive comments and Dartdoc;
they do not establish a blanket ban on comments.

### Organize declarations without adding ceremony

Keep related fields and declarations together. Separate constructors, field
groups, and methods when that improves scanning. Put simple derived properties
where they are easy to find beside related state or operations.

Do not enforce a universal field order from these examples. Simple interface
signatures can be consecutive without blank lines between every declaration.
Multiline declarations still need room around them.

## Dart client

### Formatter

The client uses:

```yaml
formatter:
  page_width: 120
  trailing_commas: preserve
```

Use trailing commas deliberately to keep argument lists and other supported
constructs vertical, even when they would fit on one line. A 120-character page
width does not mean every expression should be compressed to that width.

Use the workspace-pinned `tools/dart` formatter. Format the changed files and
inspect the result for the block spacing described above.

### Braces and expressions

Use braces for ordinary `if` statements, guards, loop bodies, and conditional
side effects, even when the body is one statement.

Avoid:

```dart
if (locator != null) updateLocator(locator);
if (changed) unawaited(flush());
```

Prefer:

```dart
if (locator != null) {
  updateLocator(locator);
}

if (changed) {
  unawaited(flush());
}
```

A short, consecutive decision table of immediate returns can remain compact,
as in the reviewed `syncLabel` getter. This is a narrow readability exception,
not permission to use inline conditionals throughout ordinary function bodies.

Simple getters, callbacks, and direct-expression methods can use `=>`. Use a block
body when the function contains meaningful steps or is clearer as an operation.
Do not replace every arrow function mechanically.

For several outcomes, prefer a switch over stacked ternaries. For example:

```dart
final status = switch (goal) {
  _ when goal.isArchived => 'Archived',
  _ when !goal.isActive => 'Paused',
  _ when goal.isCompleted => 'Target reached',
  _ => null,
};
```

### Spacing example

Give guards room while keeping single-line statements together:

```dart
final currentLocator = event.locator;
if (currentLocator != null) updateLocator(currentLocator);
final changed = eventKey != previousKey;
if (changed) unawaited(flush());
previousKey = eventKey;
```

Prefer:

```dart
final currentLocator = event.locator;

if (currentLocator != null) {
  updateLocator(currentLocator);
}

final changed = eventKey != previousKey;

if (changed) {
  unawaited(flush());
}

previousKey = eventKey;
```

Apply the same grouping to widget children: distinguish conditional sections and
multiline child widgets without inserting empty lines between every simple child.

## Python server

### Formatter and multiline calls

The server's Ruff line length is 120. Use trailing commas to retain deliberate
multiline argument layouts. Do not change formatter configuration, lint policy,
or the project's explicit typing conventions as an incidental style change.

Avoid packing a substantial result object into one call:

```python
rule = GoalRule(at=now, title=goal.title, target=goal.target_value, active=goal.is_active)
```

Prefer:

```python
rule = GoalRule(
    at=now,
    title=goal.title,
    target=goal.target_value,
    active=goal.is_active,
)
```

### Block and statement spacing

Separate blocks and multiline statements from adjacent code. Consecutive
single-line statements stay together, including query execution, result
extraction, assignments, persistence calls, and returns.

Avoid:

```python
if not request.title:
    raise ValueError("A title is required")
result = await session.execute(statement)
goal = result.scalar_one()
goal.title = request.title
await session.commit()
return goal
```

Prefer:

```python
if not request.title:
    raise ValueError("A title is required")

result = await session.execute(statement)
goal = result.scalar_one()
goal.title = request.title
await session.commit()
return goal
```

The same spacing applies to SQLAlchemy and Pydantic declarations: keep related
single-line fields together and separate a multiline field from surrounding
declarations. Do not insert a blank line solely between a docstring and its first
statement. Apply the same block-based spacing in tests.

## Limits of the examples

The review does not establish universal rules about quote style, uppercase
constant names, field ordering, or banning expression bodies. Keep the existing
language and project conventions for those choices.

The corrected samples express presentation preferences; some also contain
unfinished or behavior-changing refactors. Do not copy those changes blindly into
production code. Preserve return values, validation semantics, null safety, scope,
and type information when applying the style.

Formatters handle layout, but they cannot enforce meaningful names, block
boundaries, or helper quality. Review those manually after formatting. If a
formatter prevents a requested layout, identify the conflict rather than silently
disabling formatting or adding explanatory comments solely to influence wrapping.
