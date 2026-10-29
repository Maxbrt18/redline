#!/usr/bin/env python3
"""Vault invariants: the structure the engine relies on, checked in one command.

Run at the weekly review and on demand. Every check reads structure (YAML headers,
links, file names, PostgreSQL state, generated views), never prose. What a vault holds
its notes to (note types, reading budget) is declared in `redline.toml` under
`[vault]`; a vault's own editorial conventions belong in a script of its own.

Usage:  redline vault-check            # from the vault root
        redline vault-check --quiet    # only failures
Exit code 0 if everything passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import yaml

from redline.coach.fr import vault_check as wording
from redline.model import (
    correction_replay,
    entrypoint,
    ledger,
    paths,
    vault_layout,
)
from redline.model import instance as instance_model
from redline.model import startup_budget as budget
from redline.model.vocabulary import ThreadStatus

# The program layer needs PyYAML to read a note's frontmatter, which a bare vault
# checkout on the Mac may not have. `model.ledger` above has no dependency at all:
# the ledger stays checkable everywhere, which is the point of it being typed.
try:
    from redline.analysis import (
        ledger as ledger_gauge,  # the gauge, not the format
    )
    from redline.model import hot_cache, weekly_plan
    from redline.model.capabilities import load as load_capabilities
    from redline.model.notes import NoteError
    from redline.model.program import load as load_program
    from redline.model.strength_tables import refresh
    from redline.model.views import ViewDrift
except ImportError as exc:
    MODEL_UNAVAILABLE = str(exc)
else:
    MODEL_UNAVAILABLE = ""

SKIP_DIRS = {".git", ".obsidian", ".pytest_cache", ".venv", "inbox", "sources",
             "node_modules", "__pycache__", "server", "scripts", "templates"}

CAPABILITIES_DIR = "coaching/capabilities"
CAPABILITY_STALE_DAYS = 14     # a capability score not re-read in two weeks is stale
DECISIONS = str(vault_layout.DECISIONS)
CORRECTIONS = str(vault_layout.CORRECTIONS)
# A decision quotes the data that justified it, and decisions are append-only: a
# citation frozen in time is not a second owner.
CITES_EVIDENCE = {"coaching/decisions.md"}

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
FENCED_CODE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
# The pattern belongs to the typed ledger: a `19\d{9}` hard-coded here would silently stop
# matching once Strava ids move past that range, and the check would report "no duplicate"
# because it found nothing at all.
STRAVA_ID = re.compile(rf"\b{ledger.STRAVA_ID_PATTERN}\b")
DECISION_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _is_placeholder(name: str) -> bool:
    """A link written as an example in documentation: `[[<note>]]`, `[[…]]`."""
    return "…" in name or "<" in name or name == "..."


def _frontmatter(text: str) -> dict | None:
    """The note's YAML header; None without one or when it is not a mapping."""
    match = FRONTMATTER.search(text)
    if not match:
        return None
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return metadata if isinstance(metadata, dict) else None


def _without_code(text: str) -> str:
    """The note with its code spans and fenced blocks blanked out.

    Obsidian does not resolve a wikilink inside backticks, so neither may this: the
    weekly review writes `[[note-retirée]]` in the journal precisely to RECORD that a
    link was removed, and flagging that as a broken link is the checker crying wolf
    at its own audit trail.
    """
    return INLINE_CODE.sub(" ", FENCED_CODE.sub(" ", text))


@dataclass(frozen=True)
class Vault:
    """What every check is given: the tree, and its notes already read.

    `files` maps a vault-relative posix path to its text, for every Markdown file
    outside SKIP_DIRS. Read once, in one place, so that reads cannot diverge.
    """

    root: Path
    files: dict
    #: The vault's `redline.toml`; None when it cannot be read.
    instance: instance_model.Instance | None = None

    def text(self, relative: str):
        return self.files.get(relative)


def _detail(detail: str) -> str:
    return wording.DETAIL.format(detail=detail) if detail else ""


class Report:
    def __init__(self, quiet: bool) -> None:
        self.failed = 0
        self.warned = 0
        self.skipped = 0
        self.quiet = quiet

    def ok(self, label: str, detail: str = "") -> None:
        if not self.quiet:
            print(f"  \033[32mOK\033[0m   {label}{_detail(detail)}")

    def warn(self, label: str, detail: str = "") -> None:
        """Not a failure, but it must not read as a success either: an amber line in a
        wall of green lines is invisible when it is printed with the same OK."""
        self.warned += 1
        print(f"  \033[33m⚠\033[0m    {label}{_detail(detail)}")

    def skip(self, label: str, detail: str = "") -> None:
        """A check with nothing to look at. It says so instead of staying silent: a
        control that disappears when its target is missing reads exactly like a
        control that passed."""
        self.skipped += 1
        print(f"  \033[90m--\033[0m   {label}{_detail(detail)}")

    # Beyond this, the screen scrolls; but a silent cut is what `model/schema.py` was
    # built against ("Every error, not the first one"): you fix, re-run, there are more.
    MAX_DETAILS = 8

    def fail(self, label: str, details) -> None:
        self.failed += 1
        details = list(details)
        print(f"  \033[31mKO\033[0m   {label}")
        for line in details[:self.MAX_DETAILS]:
            print(f"         {line}")
        if len(details) > self.MAX_DETAILS:
            print(wording.MORE.format(count=len(details) - self.MAX_DETAILS))


def markdown_files(root: Path) -> dict:
    out = {}
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if SKIP_DIRS.isdisjoint(relative.parts[:-1]):
            out[relative.as_posix()] = path.read_text(encoding="utf-8")
    return out


def check_frontmatter(vault: Vault, report: Report) -> None:
    """Documentation YAML and templates are validated before they are copied."""
    files = dict(vault.files)
    template_dir = vault.root / "templates"
    if template_dir.is_dir():
        for path in template_dir.glob("*.md"):
            files[path.relative_to(vault.root).as_posix()] = path.read_text(encoding="utf-8")
    problems = []
    parsed = 0
    for path, text in sorted(files.items()):
        match = FRONTMATTER.search(text)
        if not match:
            continue
        parsed += 1
        try:
            metadata = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            problems.append(wording.YAML_INVALID.format(path=path, error=str(exc).splitlines()[0]))
            continue
        if not isinstance(metadata, dict):
            problems.append(wording.FRONTMATTER_NOT_OBJECT.format(path=path))
            continue
        note_types = vault.instance.note_types if vault.instance else None
        if note_types is not None and metadata.get("type") not in note_types:
            problems.append(wording.TYPE_UNDECLARED.format(path=path, type=metadata.get("type")))
        aliases = metadata.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            problems.append(wording.ALIASES_NOT_LIST.format(path=path))
    if problems:
        report.fail(wording.FRONTMATTER, problems)
    else:
        report.ok(wording.FRONTMATTER, wording.YAML_BLOCKS.format(parsed=parsed))


# A missing database is reported one way only, by every check that reads PostgreSQL:
# a skip. Failing on some checks and skipping on others leaves a permanently red report
# on a machine where the database does not run, and a check that cries wolf teaches
# readers to ignore red.
#
# A missing database is not a degraded mode: the app refuses to start without it and no
# fallback exists. It is simply not for the content linter to enforce.
NO_DATABASE = wording.NO_DATABASE


def _read_postgres(report: Report, label: str, read):
    """`read(store)`, or None after reporting the skip or the failure under `label`.

    One database opening for all operational checks: absence is reported one way, and a
    read failure is a KO, never a traceback.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        report.skip(label, NO_DATABASE)
        return None
    try:
        from redline.storage import Database, OperationalStore

        database = Database(url, application_name="vault-check")
        try:
            return read(OperationalStore(database))
        finally:
            database.dispose()
    except Exception as exc:
        report.fail(label,
                    [wording.POSTGRES_UNREADABLE.format(name=exc.__class__.__name__, exc=exc)])
        return None


def check_postgres_storage(vault: Vault, report: Report) -> None:
    def read(store):
        import sqlalchemy as sa

        from redline.storage.database import expected_schema_revision

        # The head is the engine's: the vault holds no migrations.
        head = expected_schema_revision(paths.ENGINE_ROOT / "alembic.ini")
        with store.database.engine.connect() as connection:
            current = connection.execute(sa.text(
                "SELECT version_num FROM alembic_version")).scalar_one_or_none()
            unpublished = int(connection.execute(sa.text(
                "SELECT dirty_since IS NOT NULL FROM redline.projection_states "
                "WHERE singleton"
            )).scalar_one_or_none() or 0)
        return head, current, unpublished, store.counts()

    state = _read_postgres(report, wording.STORAGE, read)
    if state is None:
        return
    head, current, unpublished, counts = state
    if current != head:
        report.fail(wording.STORAGE, [
            wording.STORAGE_MIGRATION.format(current=current or wording.NO_MIGRATION, head=head)])
    elif not counts["observations"]:
        report.fail(wording.STORAGE, [wording.NO_OBSERVATION])
    elif unpublished:
        report.warn(wording.STORAGE,
                    wording.STORAGE_UNPUBLISHED.format(current=current, unpublished=unpublished))
    else:
        report.ok(wording.STORAGE,
                  wording.STORAGE_OK.format(current=current, observations=counts["observations"]))


# ------------------------------------------------------------ observations PostgreSQL

def check_observations(vault: Vault, report: Report) -> None:
    """Business completeness of the canonical ledger, with no intermediate Markdown view."""
    observations = _read_postgres(report, wording.OBSERVATIONS,
                                  lambda store: store.observation_rows())
    if observations is None:
        return
    if not observations:
        report.fail(wording.OBSERVATIONS, [wording.NO_OBSERVATION])
        return
    dates = [row["occurred_on"] for row in observations]
    problems = []
    if dates != sorted(dates, reverse=True):
        problems.append(wording.OBSERVATIONS_ORDER)
    problems.extend(ledger.observation_completeness_errors(
        observations, dt.date.today()))
    if problems:
        report.fail(wording.OBSERVATIONS, problems)
    else:
        report.ok(wording.OBSERVATIONS,
                  wording.OBSERVATIONS_OK.format(observations=len(observations)))


# ----------------------------------------------------------------- one fact, one home

def check_no_duplicate_facts(vault: Vault, report: Report) -> None:
    """A canonical Strava identifier must not survive in the Git notes."""
    seen: dict[str, list[str]] = {}
    for path, text in vault.files.items():
        if path in CITES_EVIDENCE:
            continue
        for sid in set(STRAVA_ID.findall(text)):
            seen.setdefault(sid, []).append(path)
    if seen:
        report.fail(wording.NO_COPIED_FACT,
                    [wording.COPIED_FACT.format(sid=sid, join=", ".join(sorted(paths)))
                     for sid, paths in sorted(seen.items())])
    else:
        report.ok(wording.NO_COPIED_FACT)


def check_links(vault: Vault, report: Report) -> None:
    targets = set()
    for path, text in vault.files.items():
        targets.add(path.rsplit("/", 1)[-1][:-3])
        aliases = (_frontmatter(text) or {}).get("aliases")
        if isinstance(aliases, list):
            targets.update(str(alias) for alias in aliases)
    # Skipped directories are not scanned for links, but notes living there are still
    # legitimate link targets in Obsidian (e.g. sources/koopcast-selection-notes).
    for skipped in SKIP_DIRS:
        directory = vault.root / skipped
        if directory.is_dir():
            targets.update(path.stem for path in directory.rglob("*.md"))
    broken: dict[str, list[str]] = {}
    for path, text in vault.files.items():
        for raw in WIKILINK.findall(_without_code(text)):
            # Inside a Markdown table the alias pipe must be escaped -- [[ef\|EF]] is
            # valid Obsidian and must not be read as a note called "ef\".
            name = raw.split("|")[0].split("#")[0].rstrip("\\").strip()
            if not name or _is_placeholder(name) or name in targets:
                continue
            broken.setdefault(name, []).append(path)
    if broken:
        report.fail(wording.GRAPH,
                    [wording.BROKEN_LINK.format(n=n, join=", ".join(sorted(set(p))[:3]))
                     for n, p in broken.items()])
    else:
        report.ok(wording.GRAPH, wording.GRAPH_OK.format(files=len(vault.files)))


def check_unique_basenames(vault: Vault, report: Report) -> None:
    """Two notes sharing a filename make every unqualified [[wikilink]] ambiguous,
    and Obsidian resolves it by proximity -- silently, differently per linking file.

    check_links CANNOT see this: it keys its targets on basenames, so a collision
    looks like a perfectly resolvable link."""
    seen: dict[str, list[str]] = {}
    for path in vault.files:
        seen.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    clashes = {n: p for n, p in seen.items() if len(p) > 1}
    if clashes:
        report.fail(wording.UNIQUE_NAMES,
                    [wording.AMBIGUOUS_NAME.format(n=n, join=", ".join(sorted(p)), link=n[:-3])
                     for n, p in sorted(clashes.items())])
    else:
        report.ok(wording.UNIQUE_NAMES, wording.UNIQUE_NAMES_OK.format(seen=len(seen)))


def check_startup_budget(vault: Vault, report: Report) -> None:
    """The agent's reading cost, per file and per tier, as `[[vault.budget]]` declares."""
    tiers = vault.instance.read_budget if vault.instance else ()
    if not tiers:
        report.skip(wording.BUDGET, wording.NO_BUDGET)
        return
    result = budget.audit(tiers, {path: vault.text(path) for tier in tiers
                                  for path, _ in tier.files})
    if result.over or result.over_total:
        report.fail(wording.BUDGET, [*result.problems(), result.detail])
    elif result.near_total:
        report.warn(wording.BUDGET, wording.BUDGET_NEAR.format(detail=result.detail))
    else:
        report.ok(wording.BUDGET, result.detail)


def check_updated_dates(vault: Vault, report: Report) -> None:
    """`updated:` must not claim a note is fresher than its last real edit: a lying date
    is how a stale file passes for current."""
    stale = []
    for path, text in vault.files.items():
        updated = (_frontmatter(text) or {}).get("updated")
        if not isinstance(updated, dt.date):
            continue
        try:
            committed = subprocess.run(
                ["git", "log", "-1", "--format=%ad", "--date=short", "--", path],
                cwd=vault.root, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            report.skip(wording.UPDATED_DATES, wording.NO_GIT)
            return
        if not committed:
            continue  # never committed yet
        if updated.isoformat() > committed and _is_clean(vault.root, path):
            stale.append(wording.UPDATED_TOO_LATE.format(path=path, updated=updated,
                                                         committed=committed))
    if stale:
        report.fail(wording.UPDATED_DATES, stale)
    else:
        report.ok(wording.UPDATED_DATES)


def _is_clean(root: Path, path: str) -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--", path],
                         cwd=root, capture_output=True, text=True).stdout
    return not out.strip()


# -------------------------------------------------------------- structure and dates

def check_decisions_order(vault: Vault, report: Report) -> None:
    """decisions.md is append-only and declares "Ordre antéchronologique": the order
    IS the structure, so it gets checked."""
    text = vault.text(DECISIONS)
    if text is None:
        report.skip(wording.DECISIONS_ORDER, wording.DECISIONS_MISSING.format(decisions=DECISIONS))
        return
    dates = DECISION_HEADING.findall(text)
    out_of_order = [wording.DECISION_OUT_OF_ORDER.format(b=b, a=a)
                    for a, b in pairwise(dates) if b > a]
    if out_of_order:
        report.fail(wording.DECISIONS_ORDER, out_of_order)
    else:
        report.ok(wording.DECISIONS_ORDER, wording.DECISIONS_COUNT.format(dates=len(dates)))


#: Two weekly reviews to settle a due prediction before the check turns red.
PREDICTION_GRACE_DAYS = 14


def check_prediction_debt(vault: Vault, report: Report) -> None:
    """A due prediction receives its verdict: amber at first, red after two reviews."""
    rows = _read_postgres(report, wording.PREDICTIONS, lambda store: store.prediction_rows())
    if rows is None:
        return
    today = dt.date.today()
    # The store returns its rows as plain values: the date arrives as ISO text.
    rows = [{**row, "due_on": dt.date.fromisoformat(str(row["due_on"]))} for row in rows]
    due = sorted((row for row in rows if row["status"] == "open" and row["due_on"] < today),
                 key=lambda row: row["due_on"])
    lines = [wording.PREDICTION_LATE.format(prediction_key=row["prediction_key"],
                                            due_on=row["due_on"], days=(today - row["due_on"]).days)
             for row in due]
    late = [line for row, line in zip(due, lines, strict=True)
            if (today - row["due_on"]).days > PREDICTION_GRACE_DAYS]
    if late:
        report.fail(wording.PREDICTIONS,
                    [wording.PREDICTIONS_LATE.format(late=len(late),
                                                     prediction_grace_days=PREDICTION_GRACE_DAYS),
                     *late])
    elif due:
        report.warn(wording.PREDICTIONS,
                    wording.PREDICTIONS_DUE.format(due=len(due)) + " · ".join(lines[:3]))
    else:
        report.ok(wording.PREDICTIONS)


def check_threads(vault: Vault, report: Report) -> None:
    """A past follow-up stays visible without a Markdown projection."""
    rows = _read_postgres(report, wording.THREADS, lambda store: store.thread_rows())
    if rows is None:
        return
    late = [row for row in rows if row["status"] == ThreadStatus.OPEN and row["follow_up_on"]
            and row["follow_up_on"] < dt.date.today()]
    if late:
        first = late[0]
        report.warn(wording.FOLLOW_UPS,
                    wording.FOLLOW_UPS_LATE.format(late=len(late), follow_up_on=first["follow_up_on"],
                                                   body=first["body"]))
    else:
        report.ok(wording.FOLLOW_UPS)


def check_corrections_replay(vault: Vault, report: Report) -> None:
    """Every correction has a replay mode, chosen by a human.

    The owner check verifies that an owner is named; this one verifies that it was decided
    how the rule is replayed: bench scenario, code, off-bench, or `pending` debt. A new
    correction has two reviews to enter the vault's registry, then the check turns red.
    Without a registry, nothing is checked. The `pending` debt stays visible as a
    warning until scenarios are written.
    """
    text = vault.text(CORRECTIONS)
    if text is None:
        report.skip(wording.CORRECTIONS,
                    wording.CORRECTIONS_MISSING.format(corrections=CORRECTIONS))
        return
    if not (vault.root / vault_layout.CORRECTIONS_REPLAY).is_file():
        report.skip(wording.CORRECTIONS,
                    wording.NO_REPLAY_REGISTER.format(
                        corrections_replay=vault_layout.CORRECTIONS_REPLAY))
        return
    try:
        found = correction_replay.audit(vault.root, text, dt.date.today())
    except (OSError, ValueError) as exc:
        report.fail(wording.CORRECTIONS, [wording.REGISTER_UNREADABLE.format(exc=exc)])
        return
    problems = ([wording.CORRECTION_WITHOUT_MODE.format(key=c.key,
                                                        grace_days=correction_replay.GRACE_DAYS)
                 for c in found.late]
                + [wording.CORRECTION_NOT_IN_JOURNAL.format(key=key, corrections=CORRECTIONS)
                   for key in found.orphans]
                + found.broken)
    if problems:
        report.fail(wording.CORRECTIONS,
                    [wording.REGISTER.format(corrections_replay=vault_layout.CORRECTIONS_REPLAY),
                     *problems])
        return
    pending = [c.key for c in found.unregistered]
    if pending or found.owed:
        detail = []
        if pending:
            detail.append(wording.CORRECTIONS_PENDING.format(pending=len(pending),
                                                             first=pending[0]))
        if found.owed:
            detail.append(wording.CORRECTIONS_OWED.format(owed=len(found.owed)))
        report.warn(wording.CORRECTIONS, " · ".join(detail))
        return
    report.ok(wording.CORRECTIONS)


def check_deadlines(vault: Vault, report: Report) -> None:
    """An échéance past its date and still open (⬜) makes noise on its own, rather
    than waiting for someone to reread a throwaway cache."""
    rows = _read_postgres(report, wording.DEADLINES, lambda store: store.deadline_rows())
    if rows is None:
        return
    today = dt.date.today()
    late = [wording.DEADLINE_LATE.format(due_on=row["due_on"], item=row["item"])
            for row in rows if row["due_on"] < today and row["status"] == "todo"]
    if late:
        report.fail(wording.DEADLINES, late)
    else:
        report.ok(wording.DEADLINES)


def check_capability_notes(vault: Vault, report: Report) -> None:
    """Analysis notes have a stable identity and carry no operational state."""
    if MODEL_UNAVAILABLE:
        report.skip(wording.CAPABILITY_NOTES,
                    wording.MODEL_UNAVAILABLE.format(model_unavailable=MODEL_UNAVAILABLE))
        return
    try:
        capabilities = load_capabilities(vault.root)
    except NoteError as exc:
        report.fail(wording.CAPABILITY_NOTES, str(exc).splitlines())
        return
    if capabilities:
        report.ok(wording.CAPABILITY_NOTES,
                  wording.CAPABILITIES_COUNT.format(capabilities=len(capabilities)))
    else:
        report.skip(wording.CAPABILITY_NOTES,
                    wording.FOLDER_EMPTY.format(capabilities_dir=CAPABILITIES_DIR))


def check_cockpit_freshness(vault: Vault, report: Report) -> None:
    """Checks the freshness of the PostgreSQL source of truth."""
    capabilities = _read_postgres(report, wording.COCKPIT,
                                  lambda store: store.latest_capabilities())
    if capabilities is None:
        return
    if not capabilities:
        report.fail(wording.COCKPIT, [wording.NO_CAPABILITY])
        return
    today = dt.date.today()
    stale = [wording.CAPABILITY_STALE.format(slug=data["slug"],
                                             days=(today - data["evaluated_on"]).days,
                                             capability_stale_days=CAPABILITY_STALE_DAYS)
             for data in capabilities
             if (today - data["evaluated_on"]).days > CAPABILITY_STALE_DAYS]
    if stale:
        report.fail(wording.COCKPIT, stale)
    else:
        report.ok(wording.COCKPIT,
                  wording.COCKPIT_OK.format(capabilities=len(capabilities),
                                            capability_stale_days=CAPABILITY_STALE_DAYS))


def check_weekly_plans(vault: Vault, report: Report) -> None:
    """Every PostgreSQL plan validates and the current week cannot be missing."""
    if MODEL_UNAVAILABLE:
        report.skip(wording.WEEKLY_PLAN,
                    wording.MODEL_UNAVAILABLE.format(model_unavailable=MODEL_UNAVAILABLE))
        return
    current = weekly_plan.current_week()

    def read(store):
        import sqlalchemy as sa

        from redline.storage.schema import weekly_plans

        problems = []
        with store.database.engine.connect() as connection:
            weeks = list(connection.execute(sa.select(
                weekly_plans.c.iso_week).order_by(weekly_plans.c.iso_week)).scalars())
        plans = {week: store.load_plan(week) for week in weeks}
        for week, plan in plans.items():
            if plan is None:
                problems.append(wording.PLAN_NOT_FOUND.format(week=week))
                continue
            weekly_plan.validate(plan)
            revision = weekly_plan.active(plan)
            # A load entered late in a closed week enriches the archive; it cannot make
            # its prescription "to recompute". Staleness only matters for the week that
            # is currently prescribable.
            if (week == current
                    and store.plan_needs_arbitration(week, revision["revision"])):
                problems.append(wording.PLAN_RECOMPUTE.format(week=week))
        return plans, problems

    state = _read_postgres(report, wording.WEEKLY_PLAN, read)
    if state is None:
        return
    plans, problems = state
    count = sum(plan is not None for plan in plans.values())
    waiting: list[str] = []
    current_plan = plans.get(current)
    if current_plan is None:
        problems.append(wording.PLAN_MISSING.format(current=current))
    else:
        # A dated check that is due is not a vault defect: it is a question awaiting an
        # answer. Filing it with the errors teaches readers to ignore the report and to
        # confuse the two.
        waiting = [wording.PLAN_REASON.format(current=current, reason=reason)
                   for reason in weekly_plan.gate_reasons(current_plan)]
    if problems:
        report.fail(wording.WEEKLY_PLAN, problems)
    elif waiting:
        report.warn(wording.WEEKLY_PLAN, " ; ".join(waiting))
    else:
        report.ok(wording.WEEKLY_PLAN,
                  wording.WEEKLY_PLAN_OK.format(count=count, current=current))


def check_generated_views(vault: Vault, report: Report) -> None:
    """The generated views: `strength.md` exercise tables, the hot-cache form block, the
    cockpit table, **and the program blocks projected from PostgreSQL**. Fails if one
    was edited by hand instead of regenerated. NEVER repairs: a check that repairs
    silently is not a check.

    A check's success message is a promise: it names only what it actually opened.
    """
    if MODEL_UNAVAILABLE:
        report.warn(wording.VIEWS,
                    wording.MODEL_UNAVAILABLE.format(model_unavailable=MODEL_UNAVAILABLE))
        return
    if not (os.environ.get("DATABASE_URL") or "").strip():
        # Every view here is regenerated FROM PostgreSQL: without a database there is
        # nothing to compare. Letting the RuntimeError escape the `except` below would
        # abort the whole report with a traceback.
        report.skip(wording.VIEWS, NO_DATABASE)
        return
    problems = []
    try:
        problems += [wording.STRENGTH_DRIFT.format(name=name)
                     for name in refresh(vault.root, load_program(vault.root),
                                         check=True)]
        store = ledger_gauge.open_store()
        rows = ledger_gauge.parse_ledger(store)
        instruments = ledger_gauge.load_instruments(store)
        if hot_cache.refresh(vault.root, ledger_gauge, rows, instruments,
                             store=store, check=True):
            problems.append(wording.HOT_CACHE_DRIFT)
        drifted, seen = _plan_view_drift(vault)
        problems += drifted
    except (NoteError, ViewDrift, ledger.LedgerError) as exc:
        report.fail(wording.VIEWS, [str(exc).replace("\n", " ")])
        return
    if problems:
        report.fail(wording.VIEWS, problems)
    else:
        report.ok(wording.VIEWS,
                  wording.VIEWS_OK.format(seen=len(seen)))


def _plan_view_drift(vault: Vault) -> tuple:
    """(problems, number of program blocks actually opened).

    The count is returned rather than guessed: the success message cites it, so that the
    announced coverage is the observed coverage. A missing database is handled by the
    caller, which skips the whole check.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    from redline.storage import Database, OperationalStore
    from redline.storage.projector import plan_drift, plan_view_count

    database = Database(url, application_name="vault-check-plans")
    try:
        store = OperationalStore(database)
        drifted = plan_drift(store, vault.root)
        seen = plan_view_count(store, vault.root)
    finally:
        database.dispose()
    return ([wording.PLAN_DRIFT.format(name=name) for name in drifted], seen)


# The order IS the reading order of the report. Adding a check means adding it here
# and nowhere else -- there is no second list to forget.
CHECKS = (
    check_frontmatter,
    check_postgres_storage,
    check_observations,
    check_no_duplicate_facts,
    check_links,
    check_unique_basenames,
    check_startup_budget,
    check_updated_dates,
    check_decisions_order,
    check_prediction_debt,
    check_capability_notes,
    check_cockpit_freshness,
    check_weekly_plans,
    check_corrections_replay,
    check_deadlines,
    check_threads,
    check_generated_views,
)

OPERATIONAL_CHECKS = frozenset({
    check_postgres_storage, check_observations, check_prediction_debt,
    check_cockpit_freshness, check_weekly_plans, check_deadlines, check_threads,
    check_generated_views,
})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", type=Path, help="vault root (default: .)")
    parser.add_argument("--quiet", action="store_true", help="only show failures")
    parser.add_argument(
        "--static-only", action="store_true",
        help="Git checks only; says explicitly that PostgreSQL was not audited")
    args = parser.parse_args()
    root = args.root.resolve()

    print(wording.TITLE.format(root=root))
    report = Report(args.quiet)
    try:
        loaded = instance_model.load(root)
    except instance_model.InstanceError as exc:
        report.fail(wording.INSTANCE, str(exc).splitlines())
        loaded = None
    else:
        report.ok(wording.INSTANCE)
    vault = Vault(root, markdown_files(root), loaded)
    selected = [check for check in CHECKS
                if not args.static_only or check not in OPERATIONAL_CHECKS]
    for check in selected:
        check(vault, report)

    if report.failed:
        print(wording.FAILED.format(failed=report.failed))
        return 1
    if args.static_only:
        print(wording.STATIC_OK)
        return 0
    if report.warned or report.skipped:
        print(wording.STATIC_OK_WARNED.format(warned=report.warned, skipped=report.skipped))
        return 0
    print(wording.HEALTHY)
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
