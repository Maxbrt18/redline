"""The exercise tables of `coaching/strength.md`, rendered from the program.

Lives here rather than in `redline strength-tables` so that both consumers can
simply import it: the CLI that regenerates the tables, and the `redline vault-check`
control that reports a drift. A hyphenated file name is not an importable module.
"""

from __future__ import annotations

from pathlib import Path

from redline.coach.fr import strength_tables as text

from . import strength_log, vault_layout
from .program import Program
from .views import apply, table

#: Each routine's table sits between the markers of its own block in `strength.md`.
BLOCK_PREFIX = "STRENGTH_"


def blocks(program: Program) -> dict[str, str]:
    """Block name -> routine, one per routine the vault declares."""
    return {BLOCK_PREFIX + routine.upper().replace("-", "_"): routine
            for routine in program.sessions}


HEADERS = ["#", text.EXERCISE, text.DOSE, text.REST, text.SINCE]

# The log view: what was ACTUALLY done, set against the program.
LOG_BLOCK = "STRENGTH_LOG"
LOG_HEADERS = [text.EXERCISE, text.LAST_SESSIONS_TOTAL, text.SIDE_GAP, text.SCORE]
LOG_SESSIONS = 3

# Flag of the calf capacity test (`strength.md` § Instrument). It is only **displayed**,
# never interpreted: extending it from the test's standardised conditions to session sets
# is a coaching call, not a side effect of code.
GAP_FLAG = 10.0


def target(vault: Path) -> Path:
    return vault / vault_layout.STRENGTH


def render(program: Program, key: str) -> str:
    rows, dated = [], False
    for index, prescription in enumerate(program.prescriptions(key)):
        rest = f"{prescription.dose.rest_s} s" if prescription.dose.rest_s else "—"
        # The pipe is written plain: table() escapes it once, which is what Obsidian
        # needs to read a wikilink alias inside a table cell.
        link = f"[[{prescription.slug}|{prescription.name}]]"
        rows.append([index, link, prescription.dose.human(), rest,
                     prescription.since or "—"])
        dated = dated or bool(prescription.since)
    if not dated:
        return table(HEADERS[:-1], [row[:-1] for row in rows])
    return table(HEADERS, rows)


def _totals(row: dict) -> tuple:
    """(right, left, sideless) totals for one session and one exercise."""
    return (sum(strength_log.series(row.get("right"))),
            sum(strength_log.series(row.get("left"))),
            sum(strength_log.series(row.get("both"))))


def _gap(row: dict) -> str:
    """"D 37 / G 34 (−8 %)": the weaker side, quantified, or "—"."""
    right, left, _ = _totals(row)
    if not (right and left):
        return "—"
    percent = (left - right) / max(right, left) * 100
    flag = " ⚠" if abs(percent) > GAP_FLAG else ""
    return text.SIDE_BALANCE.format(right=right, left=left, percent=percent, flag=flag)


def render_log(program: Program, path: Path) -> str:
    """What the latest sessions banked, exercise by exercise.

    One line per exercise, in program order, with the set totals of the last three
    sessions (oldest -> newest). Set-by-set detail stays in the CSV: this view answers the
    only two questions asked of it, is it progressing and do both legs keep up.
    """
    rows = strength_log.rows(path)
    if not rows:
        return (text.LOG_EMPTY)

    by_slug: dict = {}
    for row in rows:
        by_slug.setdefault(row.get("slug", ""), []).append(row)

    # Programme order, not file order: the view is read next to the tables above. A
    # since-removed exercise keeps its row, at the end.
    order = []
    # Sets taken to technical FAILURE: their difficulty score is not an instrument. See
    # the Score column below.
    amrap = set()
    for key in blocks(program).values():
        for prescription in program.prescriptions(key):
            if prescription.slug not in order:
                order.append(prescription.slug)
            if prescription.dose.measure == "amrap":
                amrap.add(prescription.slug)
    order += [slug for slug in sorted(by_slug) if slug and slug not in order]

    table_rows = []
    for slug in order:
        sessions = by_slug.get(slug) or []
        if not sessions:
            continue
        recent = sessions[-LOG_SESSIONS:]
        totals = []
        for session in recent:
            right, left, plain = _totals(session)
            total = right + left + plain
            totals.append("—" if session.get("skipped") else (str(total) if total else text.DONE))
        note = program.exercises.get(slug)
        # The pipe is written plain: table() escapes it once, which Obsidian needs to
        # read a wikilink alias inside a cell.
        name = f"[[{slug}|{note['name']}]]" if note else slug
        last = recent[-1]
        # A set stopped at technical FAILURE ends near the top of the scale by construction:
        # its score does not say whether the dose is too heavy, and the "8-10 -> regress"
        # rule has no object for it (the stopping criterion IS failure). Its progression
        # datum is the COUNT, in the adjacent column. A bare score next to fixed-dose
        # exercises would make them comparable and a 10/10 would read as an alarm.
        score = text.TO_FAILURE if slug in amrap else (
            f"{last['score']}/10" if last.get("score") else "—")
        table_rows.append([name, " → ".join(totals), _gap(last), score])
    return table(LOG_HEADERS, table_rows)


def refresh(vault: Path, program: Program, *, check: bool = False) -> list:
    """Names of the blocks whose table had drifted.

    With `check`, nothing is written: a control that silently repairs what it
    should flag is not a control.
    """
    path = target(vault)
    drifted = [name for name, key in blocks(program).items()
               if apply(path, name, render(program, key), check=check)]
    log = render_log(program, vault / vault_layout.STRENGTH_LOG_CSV)
    if apply(path, LOG_BLOCK, log, check=check):
        drifted.append(LOG_BLOCK)
    return drifted
