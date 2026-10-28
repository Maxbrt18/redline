# AGENTS.md — working on the Redline engine

This file is for people and coding agents changing the engine. The coach's own
instructions are not here: they live in each instance's vault (template:
`templates/vault/CLAUDE.md`).

## Layout

| Path | What it holds |
|---|---|
| `src/redline/model/` | pure rules and typed data: workout kinds, activities, the weekly plan, the rulebook, errors |
| `src/redline/storage/` | PostgreSQL: `schema.py`, one repository module per aggregate, projections |
| `src/redline/analysis/` | computations over the ledger (load, signals, descent, feasibility) |
| `src/redline/integrations/` | Strava and weather clients |
| `src/redline/service/` | the Messenger service, agent runners, the notebook PWA (`page/`) |
| `src/redline/coach/fr/` | every athlete-facing text: prompts, messages, labels, scales |
| `src/redline/cards/` | PNG cards and charts |
| `src/redline/cli/` | one thin module per command (`week-plan.py` → `redline.cli.week_plan`) |
| `src/redline/evaluation/` | offline coaching and conversation benches |
| `migrations/` | Alembic revisions, frozen once merged |
| `deploy/` | systemd units, Caddy, host scripts, the agent CLI wrapper and its allowlist |
| `tests/` | the suite, with a fictional vault in `tests/fixtures/vault/` (athlete: Sam) |

The engine and the vault are two roots (`redline.model.paths`): code, templates and
fixtures come from `ENGINE_ROOT`; every note an instance reads or writes comes from
`vault_root()` (`VAULT_PATH`).

## No personal data

The engine is public. No real athlete name, injury, race, place, date of a coaching
history, Strava data or key. Tests use the fictional athlete Sam. Instance values come
from `redline.toml`, coaching thresholds from `rulebook.toml`; neither appears as a
literal in the engine.

## Language

- Everything developer-facing is English: identifiers, file names, comments, docstrings,
  test names, developer documentation.
- The coach speaks French. Athlete-facing text lives under `src/redline/coach/fr/`, the
  rule texts of `rulebook.default.toml`, the notebook UI strings and `templates/`.
- Some log and error messages and argparse `help=` strings are still French: new ones
  are English.

## Comments

A comment states an invariant, a constraint or a non-obvious technical reason, in the
present tense. No dates, no incident stories, no coaching decisions, no emoji: history
belongs in commit messages. `tests/test_conventions.py` enforces it on `src/`,
`deploy/`, `migrations/`, `tests/`, `.github/` and `pyproject.toml`.

## One owner per concept

- Vocabularies (workout kinds, sports, daily states) are declared once in
  `src/redline/model/` and imported, never redeclared.
- Field, column and key lists are derived from the model or the table, not written by
  hand.
- Persisted identifiers are English slugs; French labels are looked up in `coach/fr/`.

## Declare, don't infer

No regex over natural-language prose to make a decision: add a typed field instead. A
regex may validate a format (IDs, dates, slugs).

## Models and data

Parse once at the boundary (Strava, LLM output, config, frontmatter) into frozen
dataclasses and pass typed objects inward. Derived values (pace, zones, weekly load) are
computed, never stored.

## Failures

Raise the categories of `redline.model.errors` (`InputError`, `ConfigError`,
`ExternalError`, `StorageError`); programming errors stay built-ins. A CLI exits through
`redline.model.entrypoint.run`, never `sys.exit` elsewhere.

## Database

The schema changes only through a new Alembic revision; a merged revision is never
edited and never imports `redline.storage.schema`. `alembic check` must stay clean.
A data migration is rehearsed on a disposable copy before it reaches production.

## The coach's tools

The agent runs commands only through `deploy/redline-agent-cli`, and only those listed
in `deploy/agent-cli-tools.txt` (deny by default). Adding a command means a module in
`src/redline/cli/`, a line in the allowlist and a test.

## Checks

The same commands run locally and in CI:

```bash
ruff check . && mypy && vulture && pytest -q
```

PostgreSQL tests need `TEST_DATABASE_URL`; browser tests need `playwright install
chromium`. Run `git config core.hooksPath .githooks` once to enable the pre-commit hook.

## Changes

One topic per pull request, tests green, no behaviour change hidden in a refactor.
Commit messages say why, in full sentences.
