# Design: the training program moves from vault Markdown to PostgreSQL

Status: proposal, not implemented. Nothing in this note changes the engine yet.

## Summary

The training program (exercise doses, routines, session-kind targets, minimum gaps,
variants) is data, and today it lives in the YAML frontmatter of vault notes. The
engine reads it as the program of *now*: it cannot say what dose was prescribed in a
past week, because the only history is the vault's Git log, which no code reads. The
progression engine needs exactly that answer.

The proposal moves every value the engine parses out of Markdown into versioned
PostgreSQL tables, written only through `redline program …` commands, and keeps the
Markdown bodies as the coach's reasoning, linked by slug. The vault keeps read-only
projections of the program so that Obsidian browsing still works.

The line drawn is the one the project already follows for facts: **data inside Markdown
is the problem, not Markdown.** Prose an LLM rereads stays Markdown; a value code reads,
compares or sums moves to a typed store. Thresholds and rule parameters stay in
`rulebook.toml` (operator-owned, static configuration); the program is coach-written
data that changes week to week, so it belongs with the other facts in PostgreSQL.

## Why now

1. **Temporal queries.** `src/redline/analysis/progression_strength.py` says it in its
   own docstring: every exposure is read against the sheet's dose of *today*, because
   the ledger keeps no typed dose per exercise and per week. A lineage that moved from
   3×8 to 3×10 three weeks ago reads its old sessions as under-performed 3×10s. The
   progression engine cannot propose a step honestly without "what was prescribed
   when".
2. **The RPE comparison has the same flaw.** `program.rpe_targets()` reads the current
   `target_rpe` of each workout note, and `analysis/ledger.py`, `weekly_dossier.py`,
   `hot_cache.py` and `notebook_board.py` judge past sessions against it. Changing a
   target silently rewrites the verdict on every past session.
3. **Validation happens late.** A malformed dose is a turn that wrote a note; the
   engine finds out when the next card is built (`program.load` raises) or at the next
   `vault-check`. A command can refuse the write itself, with a named error, before
   anything is committed.
4. **Silent conflicts.** Two session notes may declare the same kind; `rpe_targets`
   keeps whichever it reads last. A table with a uniqueness constraint cannot hold both.
5. **The project rule already points here.** Bounds, steps and thresholds belong in
   TOML, not frontmatter; the program's prescriptions are the remaining frontmatter
   data.

## Inventory: what the engine parses as data from Markdown today

Paths are those of `src/redline/model/vault_layout.py`.

### `coaching/workouts/*.md` (frontmatter)

Read by `model/program.py` (`_workout_notes`, `load`, `rpe_targets`), validated by
`NOTE_KINDS_SCHEMA` and `SESSION_SCHEMA`.

| Field | Shape | Read by |
|---|---|---|
| `type` | `workout` | schema only |
| `kinds` | list of `session_kinds.RECORDED` | `rpe_targets` (kind → target) |
| `routine` | slug, exclusive with `kinds` | `load` (routine key), plans, ledger, strength card |
| `title`, `rule` | text printed on the card | `Program.build`, `strength_card`, notebook page |
| `exercises` | ordered list: slug, or `{exercise, dose}` override | `Program.prescriptions` |
| `target_rpe` | int 1–10 | `rpe_targets` → ledger flags, missed-quality cost, weekly dossier, hot cache, notebook board, reorganize |
| `duration_min` | int 5–120 | `Program.build` (card default) |
| `min_gap_h` | `{planned kind: hours}` | `load` (validated), reorganize |
| `variants` | `{name: {when, drop[], add[]}}` | `Program.build` |
| `capabilities`, `phase`, `contraindications`, `execution_rule`, `aliases`, `status` | lists and text | validated; `execution_rule` and `contraindications` are guidance for the coach |

The parallel removal of long-run RPE targets is assumed done: `target_rpe` no longer
exists for `long`, and this design does not reintroduce it. It stays an optional field
for the kinds and routines that keep one.

### `coaching/exercises/*.md` (frontmatter)

Read by `model/program.py` (`EXERCISE_SCHEMA`, via `notes.read_all`).

| Field | Shape | Read by |
|---|---|---|
| file name | the slug | everything that cites an exercise (`strength_results.exercise_slug`, lineages in `rulebook.toml`) |
| `name`, `target`, `cue`, `how[]`, `equipment[]` | display text | strength card, notebook page, `strength.md` tables |
| `dose` | `model/dose.py`: `measure`, `sets`, `reps`/`seconds`/`contacts`+`block`, `side`, `rest_s`; ranges as `[low, high]` | `Program.prescriptions`, `progression_strength` (current dose), `strength_tables` |
| `load` | `{kg, count}` | card |
| `impact` | bool | `Program.impact`, reorganize |
| `since` | ISO week `Wnn` without year | `strength.md` tables |
| `status`, `aliases`, `tags`, `type` | | schema; `status` filters lineage coverage |

### `coaching/capabilities/*.md` (frontmatter)

`model/capabilities.py`: `type`, `capability_number` (1–9), `aliases`, `tags`. Scores
and statuses already live in PostgreSQL (`capability_evaluations`); the notes carry
identity only, and a turn may not edit them (`service/vault.py: forbidden_in_turn`).

### `coaching/corrections.md` (headings)

`model/correction_replay.py: entries()` takes each `## ` or `### ` heading starting with
a date, or a table row starting with a date, as a correction's identity. Read by
`vault-check` (replay registry audit, owner check) and by `redline fact-write
correction`, which refuses a `--entry` that is not such a heading. The replay modes
themselves are already typed, in `coaching/corrections-replay.json`. The deliveries are
in PostgreSQL (`correction_deliveries`).

### `coaching/decisions.md` (sections)

`cli/decisions_index.py: sections()` splits on `## `; `--find` is a substring search.
`vault-check` (`check_decisions_order`) reads the dates of the `## YYYY-MM-DD` headings
and fails if they are not antichronological. Predictions already live in PostgreSQL
(`coach_predictions`).

### Calibration lines in workout notes (written, never parsed)

`src/redline/coach/fr/prompts/triggers/strava-debrief.md` (step 4) asks the coach, after
a calibration session with known RPE and reserve, to append a dated line to the
"Calibrations" section of the workout note. No engine code reads these lines, the
fixture vault has none, and their format is whatever the coach wrote: they are data
with no schema and no reader.

### Already derived, for reference

`coaching/strength.md` holds generated tables (`model/strength_tables.py`, markers
`STRENGTH_<ROUTINE>` and `STRENGTH_LOG`) rendered from the program. They are the
pattern the projections below generalise.

## Proposal

### Tables

All in schema `redline`, keyed by `athlete_id` like every other fact table. Vocabulary
checks reuse the model's lists (`session_kinds`, `dose.MEASURES`, `dose.SIDES`,
`program.STATUSES`) through the existing `_listed` / `_slug` helpers of
`storage/schema.py`.

**Identity tables** (one row per slug, never versioned):

- `program_exercises (athlete_id, slug)` — the exercise sheet's identity.
- `program_routines (athlete_id, slug)` — a strength routine (`a`, `b`, …).
- `program_session_types (athlete_id, slug)` — a non-strength session note (`easy`,
  `intervals`, …); the slug is the note's file name.

**Version tables** (append-only; each row is the full state from a week on):

- `program_exercise_versions`: `exercise_id`, `valid_from` (ISO week, `YYYY-Www`),
  `recorded_at`, `name`, `target`, `cue`, `how` (text[] ≤ 4), `equipment` (text[] ≤ 5),
  `impact`, `load_kg`, `load_count`, `status`, the dose as typed columns, `reason`
  (one line, why this version), `recorded_by` (`coach` | `athlete` | `migration`).
- `program_routine_versions`: `routine_id`, `valid_from`, `recorded_at`, `title`,
  `rule`, `duration_min`, `target_rpe`, `execution_rule`, `capabilities` (text[]),
  `phase` (text[]), `contraindications` (text[]), `status`, `reason`, `recorded_by`.
  - `program_routine_exercises (version_id, position, exercise_slug, <dose override
    columns, all nullable>)`: the order is the primary key; an override is all of a dose
    or nothing (check constraint).
  - `program_routine_gaps (version_id, kind, hours)`: `min_gap_h`, `kind` in
    `session_kinds.PLANNED`, `hours ≥ 1`.
  - `program_routine_variants (version_id, name, condition)` and
    `program_variant_changes (variant_id, op, exercise_slug)` with `op` in `drop | add`.
- `program_session_type_versions`: `session_type_id`, `valid_from`, `recorded_at`,
  `kinds` (text[] of `session_kinds.RECORDED`), `target_rpe` (nullable), `duration_min`,
  `capabilities`, `phase`, `contraindications`, `execution_rule`, `status`, `reason`,
  `recorded_by`. An exclusion constraint (or a check at the write command plus a
  `vault-check` control) keeps one active session type per kind for a given week, which
  removes the silent last-wins of `rpe_targets`.

**The dose as columns, not JSONB.** `measure`, `sets_low`, `sets_high`, `reps_low`,
`reps_high`, `seconds_low`, `seconds_high`, `contacts`, `block`, `side`, `rest_s`, with
the check constraints `dose.REQUIRED_BY_MEASURE` already states (a `time` dose has
`seconds_low`, and so on). A scalar is `low = high`. The progression engine compares and
sums doses; typed columns let it do so in SQL and make an invalid dose unstorable. The
column list is derived from `DOSE_SCHEMA` in one helper shared by the exercise and the
override tables, so the two cannot drift.

**Facts that leave Markdown:**

- `workout_calibrations`: `athlete_id`, `observation_id` (FK to the ledger, so pace,
  heart rate and weather come from the session itself and are never retyped),
  `session_type_id` or `routine_id`, `rpe` (1–10), `reserve` (unit to settle, see open
  questions), `note` (≤ 300 characters), `recorded_at`. Unique per observation.
- `coaching_corrections`: `athlete_id`, `correction_key` (the title as today), `noted_on`
  (date), `title`, `recorded_at`. It becomes the index that `correction_replay.entries()`
  and `fact-write correction` read instead of parsing headings.
  `correction_deliveries.correction_key` gains a foreign key to it.
- `coaching_decisions`: `athlete_id`, `decision_key`, `decided_on`, `title`,
  `recorded_at`. The index `decisions-index` and the order check read; the decision's
  text stays in `decisions.md` (see below). `coach_predictions` may reference it.

### Versioning semantics

- `valid_from` is an ISO week, the unit the plan already uses (`weekly_plans.iso_week`).
  The zero-padded `YYYY-Www` form sorts lexically, so "the version in force in week W"
  is the row with the greatest `valid_from ≤ W`, and among rows of that week the latest
  `recorded_at`.
- Rows are never updated or deleted. A second change in the same week is a new row with
  the same `valid_from`; the earlier one stays, so the store answers both "what was in
  force in W" and "what did the coach believe on day D" (the `recorded_at` axis).
- Retiring is a version with `status = retired`, never a deletion: old strength results
  keep their meaning.
- A version may be recorded for a future week (prepared progression); nothing reads it
  until that week.
- `strength_sessions.prescription` (the JSONB snapshot of what the page served, after
  variant, half dose and drops) stays the truth of what a given session asked. The
  version tables answer what the program prescribed; the progression engine reads the
  snapshot when it exists and the version otherwise.

A repository module `storage/repository/program.py` exposes `program_as_of(week)`,
returning the same frozen `Program` the engine uses today, so `Program.build`,
`prescriptions`, `impact` and the card code change their source, not their logic.
`rpe_targets(vault)` becomes `rpe_targets(store, week)` and every caller passes the week
of the session it judges.

### Agent commands: `redline program …`

One module, `src/redline/cli/program.py`, one line `program` in
`deploy/agent-cli/agent-cli-tools.txt` (alphabetical, between `ledger-complete` and
`progression`), tests for each subcommand. Writes take a JSON document on stdin or
`--input`, like `week-plan`, validated by the existing schemas (`EXERCISE_SCHEMA`,
`SESSION_SCHEMA`, `DOSE_SCHEMA`, `VARIANT_SCHEMA`) before any SQL. Every write prints a
read-back of what is now in force, so the coach quotes the store, not its intent.

| Command | Does |
|---|---|
| `program show [--week W] [--routine R \| --exercise S \| --type T]` | the program in force in W (default: current week) |
| `program history --exercise S` (or `--routine`, `--type`) | every version, with `valid_from`, `reason`, `recorded_by` |
| `program set-dose --exercise S --from W --reason …` | a new exercise version changing the dose only |
| `program set-exercise --from W --reason …` (JSON) | a new exercise version, any field; creates the identity on first use |
| `program set-routine --from W --reason …` (JSON) | a new routine version: order, overrides, gaps, variants, card text |
| `program set-type --from W --reason …` (JSON) | a new session-type version (`kinds`, `target_rpe`, …) |
| `program retire --exercise S --from W --reason …` | a retired version |
| `program calibrate --observation ID --rpe N --reserve N [--note …]` | a `workout_calibrations` row; the workout is resolved from the observation |
| `program correction --date D --title …` | a `coaching_corrections` row (used before `fact-write correction`) |
| `program decision --date D --title …` | a `coaching_decisions` row, written with its `## ` section |

`--from` defaults to the current ISO week; a past week is refused (the program cannot be
rewritten after the fact), except for the data migration's own writer.

`redline progression` and `week-plan` read the program through the repository; nothing
on the agent's side reads frontmatter any more.

### What stays prose in Markdown, and why

The rule: Markdown holds what is reread and reasoned about, never what is compared,
summed or validated.

- **Workout and exercise bodies** — why a session or a movement is in the program, its
  sources, its progression logic as an argument. The note keeps a minimal frontmatter
  (`type`, `aliases`, `tags`: what Obsidian needs) and its file name stays the slug, which
  is the link to the tables. The `program.py` docstring already says no code reads the
  body.
- **Decision reasoning** (`coaching/decisions.md`) — context, alternatives, why. The
  heading keeps its date and title, mirrored by the `coaching_decisions` row; the text is
  an argument the coach rereads, not a value.
- **Correction narratives** (`coaching/corrections.md`) — what the coach got wrong and
  what it should have read. Same split: identity in the table, story in the note.
- **Philosophy, protocols, concepts, episodes** — synthesis and sources, searched by
  `knowledge-search` and read whole. Nothing in them is a value code needs.
- **Capability analysis notes** — identity and reasoning only, already the case; their
  `capability_number` may move to `redline.toml` or a table later, but it is outside this
  change.

Keeping these as Markdown is deliberate: an LLM reads prose better than rows, Git diffs
of an argument are reviewable, and Obsidian links between reasoning notes are the
coach's map. Moving them would trade those for nothing code needs.

### Projections regenerated into the vault

Like `ai/hot-cache.md` and the CSV projections, rendered after every program write by
the existing view machinery (`model/views.py` markers, `regen-views`), never edited, and
restored if a turn edits them:

- In each `coaching/exercises/<slug>.md` and `coaching/workouts/<slug>.md`, a generated
  block (`<!-- PROGRAM_START -->` … `<!-- PROGRAM_END -->`) at the top of the body: the
  version in force, its `valid_from` and `reason`, and the last few versions. Obsidian
  shows the current dose next to the reasoning; the body below the block stays the
  coach's.
- `coaching/strength.md` keeps its tables, now rendered from `program_as_of(current
  week)`.
- `coaching/program.md` (new, `vault_layout.PROGRAM`): the whole program in force, one
  table per routine and one for session types, for browsing and Dataview-free reading.
- The calibrations of a workout render into its generated block, replacing the free
  "Calibrations" lines.

Whether the frontmatter itself is regenerated (so existing Obsidian Bases keep working)
is an open question; the default is no, because a generated frontmatter invites edits
that would be silently overwritten.

`service/vault.py: forbidden_in_turn` already refuses edits to canonical projections;
the generated blocks are checked the way the journal's `WEEK_PLAN` block is.

## Migration

### Alembic revision

One new revision (`migrations/versions/0009_program_tables.py` or the next free number)
creating the tables above, their constraints and indexes
(`(athlete_id, slug, valid_from DESC, recorded_at DESC)` on each version table). Written
standalone, without importing `redline.storage.schema`, like every merged revision.
`schema.py` gains the same tables so `alembic check` stays clean. The foreign key from
`correction_deliveries` to `coaching_corrections` is added in a second step, after the
data migration has filled the index (otherwise existing deliveries violate it).

### Data migration

A one-shot command (`redline storage-project`-style, not in the agent allowlist),
`redline program import --vault PATH [--dry-run]`:

1. Load the vault with today's parsers (`program.load`, `_workout_notes`,
   `correction_replay.entries`, `decisions_index.sections`), so the import reads exactly
   what the engine reads today and inherits its refusals.
2. Write one version per exercise, routine and session type with `valid_from` = the
   week of the import, `recorded_by = migration`, `reason = "imported from the vault"`.
   `since` becomes the identity's first week where it is set (it has no year: the import
   resolves it against the import date and refuses an ambiguous one).
3. Optional backfill: walk `git log` of `coaching/exercises/` and `coaching/workouts/`,
   parse each historical frontmatter, and emit a version per change with `valid_from`
   = the ISO week of the commit. This is what gives the progression engine real history
   from day one; without it, history starts at the import.
4. Index every correction heading and decision section.
5. Import calibration lines only if the athlete settles their format (open question);
   otherwise they stay in the bodies as prose and new calibrations go to the table.
6. Strip the migrated fields from the frontmatter, insert the generated blocks, commit
   the vault in one commit whose message names the migration.

Rehearsal, before production: on a disposable copy of the production database and a
clone of the vault, run the revision, `program import --dry-run`, then for real; compare
`program show` with the pre-migration `program.load` output for every routine (a test
helper serialises both to the same dict); build every strength card and diff the PNG
inputs; run `vault-check`, `redline progression` and the weekly dossier on both sides.
Only an empty diff (or a diff fully explained by the backfill) goes to production.

### Cut-over

1. Revision and import land with the readers still on Markdown; `vault-check` compares
   the two sources and fails on any difference.
2. Readers switch to the repository (`program_as_of`), one consumer per pull request:
   cards and page, then `rpe_targets` callers, then progression, then reorganize.
3. The prompt and `CLAUDE.md` switch to `redline program …`; the frontmatter fields are
   removed from the schemas and `vault-check` refuses them in a note (so a turn that
   writes a dose in frontmatter is caught rather than ignored).

## What to update

**Code**
- `src/redline/model/program.py`: schemas stay as boundary validators; `load(vault)` and
  `rpe_targets(vault)` are replaced by repository reads; `NOTE_KINDS_SCHEMA` shrinks to
  the Obsidian identity.
- `src/redline/model/capabilities.py`: unchanged.
- `src/redline/model/correction_replay.py`: `entries()` reads `coaching_corrections`;
  the heading regex survives only in the importer.
- `src/redline/model/vault_layout.py`: add `PROGRAM`; keep `WORKOUTS`, `EXERCISES` (the
  bodies still live there).
- `src/redline/storage/schema.py`, `storage/repository/program.py` (new),
  `migrations/versions/` (new revision).
- `src/redline/cli/program.py` (new), `cli/decisions_index.py` (search the index, then
  print the section from the note), `cli/fact_write.py` (`_correction` checks the table),
  `cli/ledger.py`, `cli/strength_tables.py`.
- Consumers of `program.load` / `rpe_targets`: `analysis/hot_cache.py`,
  `analysis/ledger.py`, `analysis/weekly_dossier.py`, `analysis/progression_strength.py`
  (and its docstring, whose limitation this removes), `analysis/reorganize.py`,
  `service/reorganize.py`, `service/notebook.py`, `service/notebook_board.py`,
  `service/web.py`, `cards/strength_card.py`, `model/strength_tables.py`.
- `deploy/agent-cli/agent-cli-tools.txt`: add `program`.

**`vault-check`** (`src/redline/cli/vault_check.py`)
- New: the program in PostgreSQL loads and builds every routine; every exercise and
  routine with a version has a note of the same slug and vice versa; one active session
  type per kind; generated program blocks are current; no migrated field remains in a
  frontmatter.
- Changed: `check_progression_lineages` reads sheets from the store;
  `check_corrections_replay` and `check_decisions_order` read the indexes and also check
  that each indexed entry still has its heading in the note (and the reverse).

**Prompts and templates** (French, under `src/redline/coach/fr/` and `templates/`)
- `prompts/triggers/strava-debrief.md` step 4: `redline program calibrate` instead of
  appending a line.
- `prompts/triggers/weekly.md`, `monthly.md`, `notes/depth-n2.md`, `notes/depth-n3.md`,
  `style.md`, `hot_cache.py`, `transitions.py`: wherever they point at frontmatter or
  "the workout note" for a dose or a target, point at `redline program show`.
- `templates/vault/CLAUDE.md`: the table "where things live" (`Renfo : exercices et
  séances`, corrections, decisions) and the decision procedure (write the section, then
  `program decision`).

**Docs**
- `docs/vault-format.md`: the workouts and exercises sections shrink to identity plus
  body; corrections and decisions describe the index; the new projections join
  "Written by the engine".
- `docs/architecture.md`: the program joins the PostgreSQL facts.
- `docs/llm-data.md`: `redline program` among the CLIs whose output reaches the provider.
- `CHANGELOG.md`: the entry for the release that ships it.

**Tests and fixtures**
- `tests/fixtures/vault/coaching/workouts/*.md` and `exercises/*.md`: frontmatter reduced
  to identity; a fixture loader seeds the same program into the test database (one
  source, the fixture JSON or the importer run on a frozen copy of today's notes).
- `tests/test_program.py`, `test_progression_strength.py` (a test where the dose changed
  mid-lineage and old exposures are read against their own week), `test_ledger.py`
  (a target changed later does not rewrite an old flag), `test_vault_check.py`,
  `test_correction_replay.py`, `test_fact_write_patch.py`, `test_strength_card.py`,
  `test_notebook_board.py`, `test_page.py`, `test_reorganize.py`, `test_prompts.py`
  (prompt references to `redline program`), `test_dress_rehearsal.py`,
  `test_vault_sync.py`, `test_charts.py`, `tests/conftest.py`.
- A new `tests/test_cli_program.py` and a migration test running the importer on the
  fixture vault and comparing with `program.load`.
- `tests/test_notebook_contract.py` only if the page payload changes (the aim is that it
  does not).

## Risks

- **Two sources during cut-over.** Mitigated by the comparison check in step 1 and by
  switching readers one at a time.
- **The coach loses the habit of reading the note to see the dose.** The generated block
  puts the dose at the top of the note it already opens.
- **Friction for the athlete editing in Obsidian.** A dose edited by hand in a note is no
  longer the program. `vault-check` and the generated block make that visible
  immediately; the athlete asks the coach, or an operator command, to record it.
- **Backfill accuracy.** A commit week is when a note changed, not necessarily when the
  athlete started doing it. Versions from the backfill carry `recorded_by = migration`
  so the progression engine can weigh them, and the rehearsal reviews them.

## Open questions for the athlete

1. **Backfill from Git history?** It gives the progression engine real history from day
   one, at the cost of trusting commit weeks as prescription weeks. Or start history at
   the import?
2. **Week granularity.** Is a dose change ever meant to start mid-week (from a given
   session)? If so, `valid_from` becomes a date and plans resolve it per session.
3. **Who may record a version?** Only the coach through `redline program`, or also the
   athlete from the notebook page (a "change this dose" control)?
4. **Calibrations.** What exactly is the "reserve" of a calibration line (reps in reserve,
   a 0–10 feeling, minutes left)? Are the existing lines worth importing, or do they stay
   as prose history?
5. **Obsidian Bases.** Do you rely on Bases or Dataview over workout frontmatter? If yes,
   the frontmatter could be regenerated (read-only) rather than stripped.
6. **Corrections table rows.** `corrections.md` also accepts dated table rows. Keep that
   form, or one heading per correction from now on?
7. **Session types beyond strength.** Should non-strength sessions (`easy`, `intervals`,
   hills…) get versioned structured content beyond `target_rpe` and `duration_min`
   (blocks, paces), or stay prose for now?
8. **Order of work.** Strength program first (where progression needs it), then session
   types, then corrections and decisions indexes — or all at once?
