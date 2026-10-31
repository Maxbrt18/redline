# Changelog

Each release groups what `main` gathered once it has proven itself on a real server; see
[CONTRIBUTING.md](CONTRIBUTING.md#releases). Each tag carries its date.

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
