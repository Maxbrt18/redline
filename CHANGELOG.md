# Changelog

Each release groups what `main` gathered once it has proven itself on a real server; see
[CONTRIBUTING.md](CONTRIBUTING.md#releases). Each tag carries its date.

## 0.3.0

### Added

- **Native iOS notebook**: the notebook becomes a SwiftUI app under `ios/`, connected by a
  device token bought with the `/carnet` link and kept in the keychain. Today opens on a day
  card whose colour says where the day stands, the strength session is guided set by set,
  with a Live Activity, and the app's API types are generated from
  `notebook_views.py` ([#103](https://github.com/Maxbrt18/redline/pull/103),
  [#104](https://github.com/Maxbrt18/redline/pull/104), [#105](https://github.com/Maxbrt18/redline/pull/105),
  [#106](https://github.com/Maxbrt18/redline/pull/106), [#107](https://github.com/Maxbrt18/redline/pull/107),
  [#108](https://github.com/Maxbrt18/redline/pull/108), [#109](https://github.com/Maxbrt18/redline/pull/109),
  [#110](https://github.com/Maxbrt18/redline/pull/110), [#111](https://github.com/Maxbrt18/redline/pull/111),
  [#112](https://github.com/Maxbrt18/redline/pull/112)).
- **Voice notes**: a Messenger voice note is transcribed by a speech-to-text provider
  (`TRANSCRIPTION_PROVIDER`: `groq` or `openai`) and answered like a text
  ([#100](https://github.com/Maxbrt18/redline/pull/100)).
- **Kilometre splits in the debrief**: every run is read kilometre by kilometre from its
  Strava streams, so an easy or long run that started too fast or sat above its cap shows
  even without laps ([#99](https://github.com/Maxbrt18/redline/pull/99)).
- **Long runs read against the athlete's own**: a long run's RPE is compared with the
  athlete's long runs of a comparable duration, not a fixed target
  ([#98](https://github.com/Maxbrt18/redline/pull/98), [#101](https://github.com/Maxbrt18/redline/pull/101)).
- **Unplanned sessions**: a Strava session outside the plan is recorded, so the notebook
  asks its pain, enjoyment and RPE ([#115](https://github.com/Maxbrt18/redline/pull/115)).
- **`deploy/release`**: a release in one command. The target engine's new
  `redline config-check` checks the instance's declarations first, the declarations a new
  engine needs go in with `--declarations`, and a failure puts the previous commit back;
  `--rollback` returns to the previous release
  ([#116](https://github.com/Maxbrt18/redline/pull/116)).
- **Backups watched and rehearsed**: `backup.sh` reports to its own healthchecks.io check
  (`REDLINE_BACKUP_PING_URL`), `deploy/postgres/restore-check.sh` restores an archive into a
  throwaway container and holds it to its manifest, and `self-hosting.md` states RPO and
  RTO ([#116](https://github.com/Maxbrt18/redline/pull/116)).

### Changed

- **A coach turn cannot change its own rules**: `rulebook.toml` and `redline.toml` edited
  during a turn are restored before its commands are applied
  ([#116](https://github.com/Maxbrt18/redline/pull/116)).
- **The heartbeat is the worker's**: it stops when the worker thread is gone or a job
  outlasts every timeout, and one failed job no longer stops the worker
  ([#116](https://github.com/Maxbrt18/redline/pull/116)).
- A new plan revision refuses `rpe_min`/`rpe_max` on a long run: its intensity is
  prescribed with `hr_zone` or `hr_cap_bpm`; stored revisions stay readable
  ([#98](https://github.com/Maxbrt18/redline/pull/98)).
- Strava ids up to 64 bits are accepted, and the notebook carries them as strings
  ([#113](https://github.com/Maxbrt18/redline/pull/113)).
- The architecture document matches the code again, and a design note plans the move of
  the training program from the vault to PostgreSQL
  ([#96](https://github.com/Maxbrt18/redline/pull/96), [#97](https://github.com/Maxbrt18/redline/pull/97),
  [#102](https://github.com/Maxbrt18/redline/pull/102)).

### Removed

- **The web notebook**: the page, its strength screen, `/s`, the cookie login
  (`/notebook-api/connect`) and the Messenger buttons that opened them. Those routes answer
  404; reminders go out as plain text, and `/carnet` sends the link the app connects with
  ([#114](https://github.com/Maxbrt18/redline/pull/114)).

### Upgrading

No migration. `deploy/release` checks the vault's declarations against the new engine
before anything moves; a 0.2.0 checkout does not have it yet, so take it from the tag:

```bash
git fetch --tags origin && git show v0.3.0:deploy/release > /tmp/release
bash /tmp/release v0.3.0
```

- The athlete moves to the iOS app (`ios/README.md`): the web notebook and its cookie are
  gone. Set `COACH_BASE_URL` to the public origin, the host of the `/carnet` link.
- Optional: `TRANSCRIPTION_PROVIDER` and `TRANSCRIPTION_API_KEY` for voice notes, and
  `[transcription] vocabulary` in the vault's `redline.toml`.
- Optional: the `comparable_*` and `split_*` keys of `[parameters]` (see
  `rulebook.default.toml`); without them, the long-run comparison and the split gaps stay
  silent.
- Optional: `REDLINE_BACKUP_PING_URL` in the PostgreSQL environment file.

## 0.2.0

### Added

- **Week variants and reorganisation**: variants of a week are computed from the days the
  athlete states, without the model, and the notebook reorganises the week. The coach reads
  weekly habits and days stated ahead, can be asked from the notebook, and the athlete takes
  a proposal with one tap (migrations `0006` to `0008`)
  ([#74](https://github.com/Maxbrt18/redline/pull/74), [#75](https://github.com/Maxbrt18/redline/pull/75), [#76](https://github.com/Maxbrt18/redline/pull/76), [#77](https://github.com/Maxbrt18/redline/pull/77)).
- **Fuel registry**: the form, sugars, type and minute of gut trouble of each intake are
  recorded (migration `0005`), and the notebook accepts its answers ([#72](https://github.com/Maxbrt18/redline/pull/72), [#91](https://github.com/Maxbrt18/redline/pull/91)).
- **Climbs in the race plan**: the plan detects the course's climbs and gives the time at
  their foot ([#73](https://github.com/Maxbrt18/redline/pull/73)).
- **Pain rules n3 and n4 in the engine**: persistent morning pain, poor nights or a high
  easy-run heart rate remove quality, and `week-plan revise` refuses a revision that breaks
  their verifiable part ([#79](https://github.com/Maxbrt18/redline/pull/79)). The gate's morning trend ends today in a week in progress
  ([#80](https://github.com/Maxbrt18/redline/pull/80)).
- **Pain scales declared by the instance**: `[scales]` in `rulebook.toml` gives the morning
  trace and the effort-pain scale, and the rules read them by role (base, cap, severe)
  ([#87](https://github.com/Maxbrt18/redline/pull/87)).
- **Strength counts and quality reserve**: the strength page counts the repetitions of each
  set with one tap, and a quality session's debrief asks its reserve next to the effort
  ([#88](https://github.com/Maxbrt18/redline/pull/88), [#89](https://github.com/Maxbrt18/redline/pull/89)).
- **Strength progression, read-only**: one progression engine for every family of sessions
  ([#81](https://github.com/Maxbrt18/redline/pull/81)). Strength is wired: the lineages declared in `[progression]` of `rulebook.toml`
  are read from the ledger, `redline progression` prints each proposal with its clause,
  verdicts, missing readings and ceiling, and the Sunday dossier carries them. It proposes
  only: it writes no dose and refuses no prescription ([#90](https://github.com/Maxbrt18/redline/pull/90), [#93](https://github.com/Maxbrt18/redline/pull/93), [#94](https://github.com/Maxbrt18/redline/pull/94)).

### Changed

- **The notebook's track look**: tartan header, red-line cursors, no cards, one red from the
  clock to the track ([#82](https://github.com/Maxbrt18/redline/pull/82), [#83](https://github.com/Maxbrt18/redline/pull/83), [#84](https://github.com/Maxbrt18/redline/pull/84), [#85](https://github.com/Maxbrt18/redline/pull/85), [#86](https://github.com/Maxbrt18/redline/pull/86)).
- Another sport's name stays as typed, and the day sheet scrolls on iOS ([#78](https://github.com/Maxbrt18/redline/pull/78)).
- **The engine untangled**: one owner per concept, one write path, downward layers; ISO weeks,
  formats and the proactive trigger thresholds each have a single owner ([#92](https://github.com/Maxbrt18/redline/pull/92)).
- `CONTRIBUTING.md`: every tag has its GitHub release, its notes taken from the changelog
  ([#71](https://github.com/Maxbrt18/redline/pull/71)).

### Upgrading

Back up, then run the migrations (`0005` to `0008`). The instance's `rulebook.toml` must
change **in the same step as the engine**: the previous engine refuses the new keys, and
this one refuses the old file.

- Add `[scales.morning_trace]` and `[scales.effort_pain]` (see `rulebook.default.toml`), and
  remove `pain_cap` from `[parameters]`: the effort-pain scale's `cap` replaces it.
- Add the six required `[parameters]`: `missed_after_hours`, `missed_cutoff`,
  `signal_window_days`, `low_desire_checkins`, `missed_sessions_signal`, `gear_alert_pct`
  (the defaults are in `rulebook.default.toml`).
- Optional: `[progression]` to follow strength lineages, then `redline vault-check`, which
  names the active exercise sheets still without a lineage.
- Reinstall `deploy/agent-cli/agent-cli-tools.txt`: it now lists `progression`.

## 0.1.0

### Added

- **Model identity per message**: every coach message records which model wrote it
  (`agent_turns`, migration `0002`) ([#61](https://github.com/Maxbrt18/redline/pull/61)).
- **Decision register**: each weekly-plan revision keeps what settled it (rule, judgment or
  request), the options set aside, and a snapshot of what the coach could see (migration
  `0003`) ([#62](https://github.com/Maxbrt18/redline/pull/62)).
- **Fuelling**: the carbohydrate actually taken (g/h) is recorded, a planned target is capped
  by the rulebook (rule `f1`) and names a glucose-fructose mix above a threshold (migration
  `0004`) ([#63](https://github.com/Maxbrt18/redline/pull/63)).
- **Race plan from a GPX**: `redline route-profile` and `redline race-plan` give the
  elevation profile (IGN terrain model, or the GPX offline) and passage times with their
  band, forecast and fuelling per stretch. Needs the `route` extra
  ([#64](https://github.com/Maxbrt18/redline/pull/64)).
- **Taper, measured and checked**: `ledger --week` shows the drop of a taper against the
  weeks before it, and `week-plan revise` refuses a judgment outside the band. Each typed
  race may set its own taper length and band; the rulebook fills in the rest
  ([#66](https://github.com/Maxbrt18/redline/pull/66),
  [#67](https://github.com/Maxbrt18/redline/pull/67)).
- **Race plan in the notebook**: the Race tab shows the typed race's profile and race time,
  and a plan screen retimes every passage at the pace explored, with the forecast at each
  passage ([#68](https://github.com/Maxbrt18/redline/pull/68)).

### Changed

- `fact-write deadline --patch --context` merges the given keys into the stored context; a
  key set to `null` is removed ([#69](https://github.com/Maxbrt18/redline/pull/69)).
- `CONTRIBUTING.md` says how releases are cut
  ([#65](https://github.com/Maxbrt18/redline/pull/65)).

### Upgrading

Back up, then run the migrations (`0002` to `0004`). Install with the `route` extra to use
the race plan: `pip install -e '.[route]'`.

## 0.0.2

- The documentation clones `stable`, not `main`
  ([#60](https://github.com/Maxbrt18/redline/pull/60)).
- The notebook's week line no longer unfolds a session's facts.

## 0.0.1

First public release of the engine: the Messenger service, the agent harness (Claude Code
or Codex), the PostgreSQL ledger and weekly plan, the vault contract, the notebook PWA,
the cards, and a Docker Compose setup. Published as is, without support.
