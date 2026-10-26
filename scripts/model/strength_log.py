"""Format de la projection `athlete/strength-log.csv` issue de PostgreSQL.

Toute la progression de `coaching/strength.md` repose sur deux nombres — le compte
atteint sur les séries « jusqu'à échec technique », et l'auto-notation 0-10 de la
difficulté (0-5 → on progresse · 6-7 → on tient le palier · 8-10 → on régresse).
Le fichier dit lui-même pourquoi les repos de 90 s sont prescrits : *« ces séries ne
valent que par le nombre atteint, et ce nombre n'est comparable d'une séance à
l'autre que si le repos qui le précède est le même »*.

Or jusqu'au 2026-07-31 ce nombre partait dans une **phrase française** vers la file
du coach, atterrissait dans la colonne Note d'une ligne de registre, et n'existait
**nulle part sous forme typée**. Conséquences, toutes les trois réelles : la règle de
progression n'était applicable par rien d'autre que la mémoire d'une conversation ;
la page de séance rouvrait identique chaque semaine, sans savoir ce qui avait été
fait la fois d'avant ; et le **falsifieur du choix « sans matériel »** (bilan W36,
[[decisions]] 2026-07-21) n'avait aucune série à interroger.

**Ce que ce fichier possède, et ce qu'il ne possède pas.** Il possède le détail
**par exercice** d'une séance de renfo. Il ne possède pas la séance elle-même : les
faits de niveau séance (RPE, douleur, durée) restent au [[ledger|registre]], une
ligne, comme pour toute autre séance. Un fait, un propriétaire.

**Droite et gauche ne se moyennent pas.** L'écart gauche/droite est un drapeau du
test de capacité mollet (>10 %, `strength.md`), et il est invisible dès qu'on
additionne les deux côtés. D'où deux colonnes plutôt qu'une, exclusives de `valeurs`.

L'écriture et la validation appartiennent à PostgreSQL (`strength_sets` et ses
contraintes) ; ce module ne fait que lire la projection.
"""

from __future__ import annotations

from pathlib import Path

from .views import csv_rows as rows

PATH = "athlete/strength-log.csv"
COLUMNS = ("date", "sid", "seance", "slug", "valeurs", "droite", "gauche", "score", "passe",
           "status", "completed_slots", "note")

SEPARATOR = "|"
YES = "oui"


def series(value: str | None) -> list:
    """"14|12|11" -> [14, 12, 11]."""
    return [int(part) for part in (value or "").split(SEPARATOR) if part]


def last_by_slug(path: Path) -> dict:
    """{slug: ligne} — pour chaque exercice, la DERNIÈRE séance qui l'a journalisé.

    « Dernière » se lit sur la date puis sur l'ordre du fichier : deux séances le
    même jour (A le matin, B le soir) se départagent par l'ordre d'écriture, qui est
    l'ordre réel.
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
    """Ce que la page affiche sous l'exercice en cours : « D 14·12·11 · G 13·11 — 6/10 ».

    Rendu ICI et pas dans la page, comme `Dose.human()` : une seule fonction du dépôt
    fabrique une ligne lisible, et la page ne formate rien.
    """
    if row.get("status") == "gated":
        return "retiré — injury"
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
