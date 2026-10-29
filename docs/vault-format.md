# Vault format

The vault is a Git repository of Markdown notes, the coach's memory. Most of it is free:
the coach reads it as prose, and its own `CLAUDE.md` says where things are. This page
lists what the **engine** reads or writes, the contract a vault must keep. `redline
vault-check` checks it; `tests/fixtures/vault/` is a complete, fictional example.

Paths are relative to the vault root and declared once, in
`src/redline/model/vault_layout.py`.

## At the root

| File | Owner | Content |
|---|---|---|
| `redline.toml` | you | the instance: athlete, heart rates, time zone, injuries, capabilities, optional `[ledger]` and `[vault]`. Documented field by field in `config/redline.example.toml`. Required: the service does not start without it |
| `rulebook.toml` | you, optional | the coaching rules in force and their thresholds. Replaces `src/redline/model/rulebook.default.toml` entirely |
| `CLAUDE.md` | you | the coach's instructions, read at the start of every session. Template: `templates/vault/CLAUDE.md` |
| `AGENTS.md` | you, optional | the same for Codex, which loads it natively |

Changing `CLAUDE.md`, `AGENTS.md` or `redline.toml` starts a new agent session at the
next turn; changing a note does not.

## Written by the engine

These files show PostgreSQL facts. They are regenerated after each change and **never
edited**: a turn that edits one sees it restored.

| Path | Content |
|---|---|
| `ai/hot-cache.md` | the summary the coach reads first; between `<!-- …_START -->` and `<!-- …_END -->` markers, the engine's blocks |
| `athlete/journal/<yyyy>-w<nn>.md` | one note per ISO week; the engine writes the plan between `<!-- WEEK_PLAN_START -->` and `<!-- WEEK_PLAN_END -->`, the rest is the coach's review |
| `athlete/daily.csv` | the daily state: sleep, life load, desire |
| `athlete/instruments.csv` | the weekly instruments |
| `athlete/strength-log.csv` | what was done in each strength session |

The engine creates a week's journal when that week gets a plan.

## Read by the engine

### Workouts: `coaching/workouts/*.md`

One note per kind of session, or per strength routine. The frontmatter is the contract;
the body is the reasoning, never parsed.

A **session note** names the session kinds it describes, among the engine's kinds:
`easy`, `long`, `threshold`, `hills`, `strides`, `cadence`, `race`, `hike`, `bike`,
`strength` (the list is `src/redline/model/session_kinds.py`).

```yaml
---
type: workout
kinds: [easy]
capabilities: [endurance]
target_rpe: 3        # the effort the coach compares the session with
duration_min: 45
phase: [base, general]
status: active       # active | retired
---
```

A **strength routine** declares `routine:` instead of `kinds:`, and its exercises in
order:

```yaml
---
type: workout
routine: a                   # the identifier the plan and the ledger cite
title: "Strength, session A" # printed at the top of the card
rule: "Stop two reps before failure."   # printed at the bottom: what the athlete reads during it
exercises:
  - warmup-a                 # an exercise note, with its own dose
  - exercise: calf-drop      # or a slug with the dose of this session
    dose: {measure: reps, sets: 3, reps: 8, side: each, rest_s: 90}
target_rpe: 6
min_gap_h: {strength: 48}    # minimum hours since another session of a kind
variants:
  degraded:
    when: "pain above 2/10"
    drop: [calf-drop]
    add: [calf-iso]
status: active
---
```

Optional fields of both: `aliases`, `capabilities`, `duration_min`, `phase`,
`contraindications`, `execution_rule` (guidance for the coach, not printed).

### Exercises: `coaching/exercises/*.md`

One note per exercise, named by its slug (`[a-z0-9-]`, 40 characters at most):

```yaml
---
type: exercise
name: "Single-leg calf drop"
cue: "Five seconds down; up on both feet."   # one line on the card
equipment: ["Step"]
target: "soleus, eccentric"
how:                                          # up to four instructions, as reread between sets
  - "Five seconds down, below the step."
  - "Up on both feet: only the way down counts."
dose:
  measure: reps        # reps | time | amrap | contacts
  sets: 3              # a number or a [low, high] range
  reps: 8              # per set; `seconds` for time, `contacts` and `block` for contacts
  side: each           # each | both
  rest_s: 90
since: W32             # ISO week it entered the programme, optional
status: active
---
```

### Capabilities: `coaching/capabilities/*.md`

One analysis note per capability declared in `redline.toml`. The frontmatter carries
its identity only; its score and status live in PostgreSQL (`redline fact-write
capability`):

```yaml
---
type: capability
capability_number: 1     # 1 to 9
aliases: [endurance]
---
```

### Decisions and corrections

- `coaching/decisions.md`: prose, one decision per `## ` section. `redline
  decisions-index --find <term>` searches the sections; the predictions they carry live
  in PostgreSQL.
- `coaching/corrections.md`: the coach's errors corrected by the athlete, each a
  dated `## ` or `### ` heading (`## 2030-03-11 — …`) or a table row starting with the
  date. `redline fact-write correction --entry` names one by its title.
- `coaching/corrections-replay.json`, optional: how each correction is replayed, so a
  fixed error stays fixed. A list of objects:

  ```json
  [{"correction": "2030-03-11 — Volume quoted from memory", "replay": "scenario",
    "scenarios": ["volume-is-computed"]}]
  ```

  `replay` is `scenario` (with `scenarios`), `code` (with `carriers`, the files that
  enforce it), `off-bench` (with a `note`: nothing to replay) or `pending` (with a
  `note`: a scenario is still owed). A correction left without a mode for 14 days
  fails `vault-check`.

### Knowledge: `concepts/`, `episodes/`

The notes `redline knowledge-search` indexes: concepts (a synthesis, then what the
sources say) and episodes (a source, a session, an event). Their content is free.

## Checked by `vault-check`

- every frontmatter is valid YAML, and `aliases` a list;
- if `[vault] note_types` is declared in `redline.toml`, every note's `type` is one of
  them;
- every `[[link]]` resolves, and note names are unique;
- the generated views are up to date, and no fact is duplicated in a note;
- with `[[vault.budget]]`, the files paid at each turn stay within their word budget;
- with PostgreSQL: the plans, the observations, the threads, the deadlines, the
  predictions and the capabilities are consistent with their notes.
