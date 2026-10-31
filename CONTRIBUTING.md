# Contributing

Redline is published as is, without support, and it moves with the needs of the runner who
uses it, so an issue or a pull request may stay unanswered. A small, focused fix is the most likely to land. For anything larger,
open an issue first, before you spend time on it.

## Set up

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -c requirements.txt -c requirements-dev.txt -e '.[cards,codex,dev]'
playwright install chromium
git config core.hooksPath .githooks
```

The suite runs against a fictional athlete, whose vault is in `tests/fixtures/vault/`.
Tests that need PostgreSQL read `TEST_DATABASE_URL`, and any that skip say why.

## Check your work

```bash
ruff check . && mypy && vulture && pytest -q -rs
```

The CI runs the same commands, then runs the suite again as the service's database role.

## The rules that matter

[`AGENTS.md`](AGENTS.md) holds the conventions. The ones a pull request is most likely to
meet:

- **No personal data.** No real athlete, injury, race, place, date of a coaching history,
  Strava data or key. `tests/test_personal_data.py` checks every tracked file.
- **English for developers, French for the athlete.** What the coach or the athlete reads
  lives in `src/redline/coach/fr/` or in the notebook page.
- **Comments explain why.** They state an invariant or a reason, in the present tense.
  History belongs in commit messages.
- **The schema moves by a new Alembic revision.** A merged revision is never edited.
- **No regex over prose to make a decision.** Add a typed field instead.

## Pull requests

Keep one change per pull request, with tests. Pull requests are squashed into `main`, so
the title should say what is now true. `main` runs on a real server before it becomes a
release.

## Releases

A release groups what `main` has gathered, never a single merge. It is cut once that work
has proven itself on the real server: migrations applied, features used, no known
regression. The version follows SemVer for 0.x: `0.x.0` when it brings features or a
migration, `0.x.y` when it only fixes. Each release has its entry in
[`CHANGELOG.md`](CHANGELOG.md), merged before the tag. `stable` then moves forward to that
commit, which gets an annotated `v0.x.y` tag; it never receives a merge.

## License

By contributing, you agree that your work is licensed under the
[GNU AGPL v3.0 or later](LICENSE), like the rest of the project.
