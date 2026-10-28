"""La ligne « Forme » de `ai/hot-cache.md`, rendue depuis le registre.

Cette ligne seulement : sur les ~900 mots du bloc « Où on en est », elle seule est de
l'état dérivé. Le reste est du jugement éditorial, et le générer reviendrait à
dériver de la prose depuis de la prose — ce que l'anti-POC de CLAUDE.md interdit.

Ce que ça achète n'est pas de la place mais la non-dérive : tapés à la main, ces
chiffres devenaient faux en silence à chaque ligne ajoutée au registre.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import programme, vault_layout
from .views import apply

if TYPE_CHECKING:
    from scripts.storage import OperationalStore

BLOCK = "FORME"
#: Le second bloc généré : l'état qui décide des prochaines semaines, recalculé à chaque
#: projection. Ajouté le 2026-09-27 : la prose manuelle qui le portait avait deux
#: semaines de retard (« zéro transition jusqu'à W38 », une confrontation « jamais
#: écrite d'avance » déjà choisie) dans le seul fichier lu à chaque tour.
STATE_BLOCK = "ETAT"


def target(vault: Path) -> Path:
    return vault / vault_layout.HOT_CACHE


def _round(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def render(ledger, rows: list, instruments: dict, targets: dict,
           today: dt.date | None = None, zones: list | None = None) -> str:
    """Le paragraphe Forme pour la dernière semaine du registre.

    `ledger` est le module `scripts/ledger.py`, passé en argument plutôt qu'importé :
    `model/` ne dépend de rien au-dessus de lui, et c'est ce qui garde cette couche
    importable sur une machine sans dépendances.
    """
    per_week = ledger.eq_hours_per_week(rows, instruments, targets)
    if not per_week:
        return "- **Forme** : registre vide."
    # Une semaine dont toutes les lignes sont à poids nul (une lecture spontanée
    # posée avant la première séance de la semaine, par exemple) n'est pas encore
    # « bouclée » : elle n'a juste pas commencé. On rend la dernière semaine qui
    # porte un vrai poids, sinon (registre neuf) la plus récente quand même.
    weighted = {label: h for label, h in per_week.items() if h > 0}
    label = max(weighted) if weighted else max(per_week)
    week = [row for row in rows if row.week == label]

    instr = instruments.get(label, ledger.Instruments())
    h_eq, level, _ = ledger.equivalent_hours(week, instr, targets)
    ratio = ledger.acute_chronic_ratio(per_week, label)

    # Le volume vient de `ledger.week_volume` et de nulle part ailleurs : cette ligne et
    # `ledger.py --semaine` doivent dire le même kilométrage par construction, pas par
    # coïncidence de deux sommes écrites à deux endroits.
    volume = ledger.week_volume(week)
    run_km, walk_km, runs = volume.km_run, volume.km_walk, volume.runs

    # La part de marche dans la jauge : c'est elle qui dit si un rouge vient de la blessure
    # ou n'est que systémique. La calculer ici évite de la refaire de tête chaque semaine.
    walk_h = sum(ledger.row_weight(row, targets)[0] for row in week
                 if row.type in ledger.WALK_TYPES)
    base = sum(ledger.row_weight(row, targets)[0] for row in week) or 1.0
    walk_share = round(100 * walk_h / base)

    # Deux séries, jamais fusionnées : « pendant l'effort » et « au repos » ne
    # répondent pas à la même question, et la règle Esculier a besoin des deux.
    def peak(series) -> str:
        return _round(series.peak) if series.n else "—"

    # Une tendance par zone surveillée, jamais une seule pour toutes.
    trends = [(zone.label, " → ".join(
        f"{w.week.split('-')[-1]} {peak(w.effort)}/{peak(w.rest)}"
        for w in ledger.pain_trend(rows, 3, zone.slug))) for zone in zones or []]
    trend = (" · ".join(f"{label} {text or '—'}" for label, text in trends) if len(trends) > 1
             else (trends[0][1] or "—") if trends else "aucune zone surveillée")

    red = []
    if level > ledger.LEVEL_CAP:
        red.append("niveau")
    if ratio and ratio > ledger.RATIO_CAP:
        red.append("ratio")
    verdict = f" — **{' et '.join(red)} au rouge**" if red else ""
    walk_note = (f", dont **{walk_share} % de marche**" if walk_share >= 25 else "")

    short = label.split("-")[-1]          # « 2026-W30 » -> « W30 »
    sorties = f"{runs} sortie" + ("s" if runs > 1 else "")
    # ⭐ « Bouclée » seulement une fois la semaine CLOSE (2026-09-27). Le samedi 26/09,
    # ce bloc lu à chaque tour annonçait « W39 bouclée — 13 km, ratio 0,3 » : un
    # effondrement fabriqué par deux jours manquants, le piège exact que `ledger.py`
    # avait déjà corrigé pour la trajectoire le 20/08 (objection de l'athlète).
    day = today or dt.date.today()
    iso = day.isocalendar()
    if label == f"{iso.year}-W{iso.week:02d}":
        # Rien qui change chaque jour : la vue est contrôlée contre sa régénération,
        # et une date du jour la ferait dériver quotidiennement sans rien apprendre.
        state = f"{short} en cours, semaine ouverte, ratio partiel"
    else:
        state = f"{short} bouclée"
    return (
        f"- **Forme** : **{state}** — {_round(run_km)} km courus ({sorties})"
        + (f" + {_round(walk_km)} km marchés" if walk_km else "")
        + f". **Tendance douleur, au PIC hebdo** (jamais la médiane), "
        f"**effort/repos** : {trend}. "
        f"**Jauge : elle se CALCULE, ne la réécris jamais de tête** — "
        f"`python3 scripts/ledger.py --semaine <W>`. {short} = "
        f"**{_round(h_eq)} h-éq · {level}/100 · ratio "
        f"{_round(ratio) if ratio is not None else '—'}**{verdict}{walk_note}. "
        f"Faits : PostgreSQL. Bloc **généré** par `scripts/hot-cache.py`."
    )


def _short(text: str, limit: int = 60) -> str:
    """Le bloc est lu à chaque tour et compte dans le budget du hot-cache."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def render_state(ledger, rows: list, *, deadlines: list[dict], capabilities: list[dict],
                 typed_due: int, race_on: dt.date | None,
                 today: dt.date) -> str:
    """Le bloc ÉTAT : ce qui décide des prochaines semaines, calculé et daté."""
    # Aucune date du jour dans le rendu : il ne change qu'avec les faits ou la semaine.
    lines = ["- **État calculé** depuis PostgreSQL et decisions.md par "
             "`scripts/hot-cache.py`, ne pas l'éditer."]
    trajectory = []
    longest = ledger.longest_recent(rows, today)
    if longest is not None:
        trajectory.append(f"plus longue des 30 j **{_round(longest)} km**")
    stall = ledger.long_run_stall(rows, today)
    if stall is not None and stall.weeks:
        flag = " 🔴 protocole des baisses ouvert (s9)" if stall.triggered else ""
        trajectory.append(f"sans nouveau record depuis {stall.weeks} semaine(s) close(s){flag}")
    if race_on is not None:
        weeks = (ledger._week_start(ledger._week_label(race_on))
                 - ledger._week_start(ledger._week_label(today))).days // 7
        trajectory.append(f"course le {race_on:%d/%m}, dans {weeks} semaine(s)")
    if trajectory:
        lines.append("- **Trajectoire** : " + " · ".join(trajectory) + ".")
    overdue = [row for row in deadlines
               if row.get("status") != "done" and _as_date(row["due_on"]) < today]
    if overdue:
        shown = " · ".join(f"{row['deadline_key']} ({_as_date(row['due_on']):%d/%m})"
                           + (f", bloque {_short(row['blocks'])}" if row.get("blocks") else "")
                           for row in overdue[:4])
        more = f" · +{len(overdue) - 4}" if len(overdue) > 4 else ""
        lines.append(f"- **Échéances échues** : {shown}{more}.")
    if capabilities:
        lines.append("- **Capacités** : " + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities) + ".")
    if typed_due:
        lines.append(f"- **Prédictions à solder** : {typed_due} "
                     "(`fact-read.py predictions --due`).")
    return "\n".join(lines)


def state_inputs(store: OperationalStore, vault: Path, today: dt.date) -> dict:
    """Lit ce que le bloc ÉTAT affiche, chez chaque propriétaire."""
    predictions = store.prediction_rows()
    typed_due = sum(1 for row in predictions if row.get("status") == "open"
                    and _as_date(row["due_on"]) <= today)
    deadlines = store.deadline_rows()
    races = [_as_date(row["due_on"]) for row in deadlines
             if row.get("kind") == "race" and row.get("status") != "done"
             and _as_date(row["due_on"]) > today]
    return {"deadlines": deadlines, "capabilities": store.latest_capabilities(),
            "typed_due": typed_due,
            "race_on": min(races) if races else None}


def refresh(vault: Path, ledger, rows: list, instruments: dict,
            *, store: Any = None, check: bool = False,
            today: dt.date | None = None) -> bool:
    """True si un bloc avait dérivé. Avec `check`, rien n'est écrit.

    Les cibles de RPE se résolvent ICI, depuis le vault : le bloc Forme doit rendre
    exactement la même jauge que `ledger.py --semaine`, et une seconde source de
    cibles suffirait à les faire diverger en silence (2026-08-08). Le bloc ÉTAT n'est
    rendu que si le fichier porte ses marqueurs et qu'un store est fourni.
    """
    day = today or dt.date.today()
    targets = programme.rpe_targets(vault)
    zones = ledger.pain_zones(store) if store is not None else []
    drifted = apply(target(vault), BLOCK,
                    render(ledger, rows, instruments, targets, today=day, zones=zones),
                    check=check)
    path = target(vault)
    if store is not None and f"<!-- {STATE_BLOCK}_START -->" in path.read_text(
            encoding="utf-8"):
        state = render_state(ledger, rows, today=day, **state_inputs(store, vault, day))
        drifted = apply(path, STATE_BLOCK, state, check=check) or drifted
    return drifted
