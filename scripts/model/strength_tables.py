"""The exercise tables of `coaching/strength.md`, rendered from the programme.

Lives here rather than in `scripts/strength-tables.py` so that both consumers can
simply import it: the CLI that regenerates the tables, and the `vault-check.py`
control that reports a drift. A hyphenated file name is not an importable module,
which is why the control used to reach for `importlib.spec_from_file_location`.
"""

from __future__ import annotations

from pathlib import Path

from . import strength_log
from .programme import Programme
from .views import apply, table

#: Each routine's table sits between the markers of its own block in `strength.md`.
BLOCK_PREFIX = "RENFO_"


def blocks(programme: Programme) -> dict[str, str]:
    """Block name -> routine, one per routine the vault declares."""
    return {BLOCK_PREFIX + routine.upper().replace("-", "_"): routine
            for routine in programme.sessions}


HEADERS = ["#", "Exercice", "Dose", "Repos", "Depuis"]

# La vue du journal : ce qui a été RÉELLEMENT fait, en face du programme.
LOG_BLOCK = "RENFO_JOURNAL"
LOG_HEADERS = ["Exercice", "Total, 3 dernières séances", "Écart G/D", "Score"]
LOG_SESSIONS = 3

# Le drapeau du test de capacité mollet (`strength.md` § Instrument). Il est
# **affiché**, jamais interprété : l'étendre des conditions standardisées du test
# aux séries de séance est un arbitrage de coaching, pas un effet de bord de code.
GAP_FLAG = 10.0


def target(vault: Path) -> Path:
    return vault / "coaching" / "strength.md"


def render(programme: Programme, key: str) -> str:
    rows, dated = [], False
    for index, prescription in enumerate(programme.prescriptions(key)):
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
    """(droite, gauche, sans côté) pour une séance et un exercice."""
    return (sum(strength_log.series(row.get("droite"))),
            sum(strength_log.series(row.get("gauche"))),
            sum(strength_log.series(row.get("valeurs"))))


def _gap(row: dict) -> str:
    """« D 37 / G 34 (−8 %) » — le côté faible, chiffré, ou « — »."""
    right, left, _ = _totals(row)
    if not (right and left):
        return "—"
    percent = (left - right) / max(right, left) * 100
    flag = " ⚠" if abs(percent) > GAP_FLAG else ""
    return f"D {right} / G {left} ({percent:+.0f} %){flag}"


def render_log(programme: Programme, path: Path) -> str:
    """Ce que les dernières séances ont banqué, exercice par exercice.

    Une ligne par exercice, dans l'ordre du programme, avec le total des séries des
    trois dernières séances (ancienne → récente). Le détail série par série reste
    dans le CSV : cette vue répond aux deux seules questions qu'on lui pose — est-ce
    que ça progresse, et est-ce que les deux jambes suivent.
    """
    rows = strength_log.rows(path)
    if not rows:
        return ("*Aucune séance journalisée pour l'instant — le journal se remplit "
                "tout seul à chaque compte-rendu de la page de séance.*")

    by_slug: dict = {}
    for row in rows:
        by_slug.setdefault(row.get("slug", ""), []).append(row)

    # L'ordre du programme, pas celui du fichier : la vue se lit en face des tables
    # ci-dessus. Un exercice retiré depuis garde sa ligne, à la fin.
    order = []
    # Les séries menées À L'ÉCHEC technique : leur score de difficulté n'est pas un
    # instrument. Voir la colonne Score plus bas.
    amrap = set()
    for key in blocks(programme).values():
        for prescription in programme.prescriptions(key):
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
            totals.append("—" if session.get("passe") else (str(total) if total else "fait"))
        note = programme.exercises.get(slug)
        # Le pipe s'écrit nu : table() l'échappe une fois, ce dont Obsidian a
        # besoin pour lire un alias de wikilink dans une cellule.
        name = f"[[{slug}|{note['name']}]]" if note else slug
        last = recent[-1]
        # Une série qu'on arrête À L'ÉCHEC technique finit par construction près du haut
        # de l'échelle : son score ne dit pas si la dose est trop lourde, et la règle
        # « 8-10 → on régresse » n'a même pas d'objet pour elle (régresser quoi ? le
        # critère d'arrêt EST l'échec). Sa donnée de progression est le COMPTE, dans la
        # colonne d'à côté, comme le disent les fiches. Afficher un score nu à côté des
        # exercices à dose fixe les rendait comparables, et un 10/10 s'y lisait comme
        # une alarme — c'est arrivé au bilan du 2026-08-20 (coaching/corrections.md).
        score = "à l'échec" if slug in amrap else (
            f"{last['score']}/10" if last.get("score") else "—")
        table_rows.append([name, " → ".join(totals), _gap(last), score])
    return table(LOG_HEADERS, table_rows)


def refresh(vault: Path, programme: Programme, *, check: bool = False) -> list:
    """Names of the blocks whose table had drifted.

    With `check`, nothing is written: a control that silently repairs what it
    should flag is not a control.
    """
    path = target(vault)
    drifted = [name for name, key in blocks(programme).items()
               if apply(path, name, render(programme, key), check=check)]
    log = render_log(programme, vault / strength_log.PATH)
    if apply(path, LOG_BLOCK, log, check=check):
        drifted.append(LOG_BLOCK)
    return drifted
