"""Format of the `athlete/strength-log.csv` projection from PostgreSQL.

The whole strength progression rests on two numbers: the count reached on the sets
taken "to technical failure", and the 0-10 self-rated difficulty (0-5 -> progress,
6-7 -> hold the step, 8-10 -> regress). The count is only comparable from one session
to the next if the rest that precedes it is the same.

Those numbers must exist in typed form: otherwise the progression rule can only be
applied from the memory of a conversation, and the session page cannot know what was
done last time.

What this file owns, and what it does not. It owns the per-exercise detail of a
strength session. It does not own the session itself: session-level facts (RPE, pain,
duration) stay in the ledger, one row, like any other session. One fact, one owner.

Right and left are never averaged. The left/right gap is a flag of the calf capacity
test (>10 %), and it disappears as soon as both sides are added. Hence two columns
rather than one, exclusive of `valeurs`.

Writing and validation belong to PostgreSQL (`strength_sets` and its constraints); this
module only reads the projection.
"""

from __future__ import annotations

from pathlib import Path

from .views import csv_rows as rows

COLUMNS = ("date", "sid", "seance", "slug", "valeurs", "droite", "gauche", "score", "passe",
           "status", "completed_slots", "note")

SEPARATOR = "|"
YES = "oui"


def series(value: str | None) -> list:
    """"14|12|11" -> [14, 12, 11]."""
    return [int(part) for part in (value or "").split(SEPARATOR) if part]


def last_by_slug(path: Path) -> dict:
    """{slug: row} - for each exercise, the LAST session that logged it.

    "Last" is read on the date, then on file order: two sessions on the same day are
    ordered by write order, which is the real order.
    """
    latest: dict = {}
    for row in rows(path):
        slug = row.get("slug")
        if not slug:
            continue
        known = latest.get(slug)
        if known is None or row.get("date", "") >= known.get("date", ""):
            latest[slug] = row
    return latest


def human(row: dict, limit: int = 64) -> str:
    """What the page shows under the current exercise: `D 14·12·11 · G 13·11 — 6/10`.

    Rendered HERE and not in the page, like `Dose.human()`: a single function builds the
    readable line, and the page formats nothing.
    """
    if row.get("status") == "gated":
        return "retiré — douleur"
    if row.get("passe"):
        return "pas fait"
    parts = []
    for label, column in (("D", "droite"), ("G", "gauche")):
        if row.get(column):
            parts.append(f"{label} {'·'.join(str(n) for n in series(row[column]))}")
    if row.get("valeurs"):
        parts.append("·".join(str(n) for n in series(row["valeurs"])))
    line = " · ".join(parts)
    if row.get("status") == "partial":
        line = f"partiel · {line}" if line else "partiel"
    if row.get("score"):
        line = f"{line} — {row['score']}/10" if line else f"{row['score']}/10"
    return line[:limit]
