#!/usr/bin/env python3
"""Create/revise/show/check the canonical weekly training plan.

Codes de sortie de `check` et `show`, et la distinction porte tout le reste :

- ``0`` prescriptible ;
- ``2`` valide mais EN ATTENTE D'UNE RÉPONSE DE SAM — un contrôle daté est échu.
  Le programme est intact, `show` l'imprime quand même sur stdout, et la conduite à
  tenir est de POSER la question du contrôle (elle est sur stderr), pas de réparer
  quoi que ce soit ;
- ``3`` valide mais À RÉEXAMINER — une activité non rattachée à une séance du plan est
  apparue depuis son calcul. Intact et imprimé lui aussi ; la conduite à tenir est
  d'arbitrer cette charge imprévue, pas de recopier les séances terminées ;
- ``1`` invalide : absent ou illisible. Là seulement, rien n'est lisible et rien ne
  sort.

Confondre 2 et 1 supprimait le mot du matin les jours de contrôle (2026-08-12).
Confondre 3 et 1 faisait pire (2026-08-14) : la CLI rendait « invalide » avec un
stdout VIDE pendant que le service rendait « périmé, tu peux répondre ». Le coach
lançait `show`, ne voyait rien, et concluait que son programme était cassé — alors
qu'il l'avait sous les yeux dans son prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import agent_input, commands  # noqa: E402
from scripts.model.notes import NoteError  # noqa: E402
from scripts.model.schema import SchemaError  # noqa: E402
from scripts.model.weekly_plan import (  # noqa: E402
    active,
    as_json,
    change_evidence_errors,
    coherence_errors,
    current_week,
    gate_reasons,
    intention_errors,
    now_stamp,
    revision_readback,
    typed_target_errors,
    validate,
    written_text_errors,
)
from scripts.storage import StorageError  # noqa: E402


def _distance_notes(store, revision: dict) -> str:
    """L'écart aux 30 jours de chaque course à venir (garde-fou n°8, informatif)."""
    import datetime as dt

    from scripts import ledger
    try:
        rows = ledger.parse_ledger(store)
    except Exception as exc:
        return f"Écart aux 30 jours : lecture du registre impossible ({exc.__class__.__name__})."
    # L'horodatage de la révision est celui du service (`now_stamp`) : c'est le jour
    # depuis lequel « à venir » se juge, pas l'horloge de la machine qui relit.
    written_on = dt.date.fromisoformat(str(revision["computed_at"])[:10])
    notes = ledger.transition_notes(rows, revision["sessions"], written_on)
    if not notes:
        return ""
    return "\n".join(["Écart aux 30 jours (garde-fou n°8, informatif, jamais bloquant) :",
                      *(f"  - {note}" for note in notes)])


EXIT_OK = 0
EXIT_INVALID = 1
EXIT_AWAITING_GATE = 2
EXIT_STALE = 3


def _database_store():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL obligatoire : le journal ne contient qu'une projection")
    from scripts.storage import Database, OperationalStore
    return OperationalStore(Database(url, application_name="week-plan"))


def _database_command(store, command: str, week: str, optional: bool,
                      input_path: str | None = None) -> int:
    if command in {"revise", "preview"}:
        # Un tour du coach remet son JSON par `--input`, jamais par `<` : voir
        # `scripts/model/agent_input.py`. stdin reste la voie des lancements opérateur.
        revision = agent_input.read_json(input_path)
        existing = store.load_plan(week)
        expected = 1 if existing is None else len(existing["revisions"]) + 1
        if revision.get("revision") != expected:
            raise SchemaError([f"revision : attendu {expected}, reçu {revision.get('revision')!r}"])
        current = dict(revision)
        # ⭐ L'HORODATAGE APPARTIENT AU SERVICE, PLUS AU MODÈLE (2026-08-25). Le champ
        # n'était vérifié que sur sa FORME : les deux révisions du 25/08 portent minuit
        # et midi UTC, toutes deux écrites à 21h13. Un coach qui invente l'heure qu'il
        # est invente aussi le jour dont Sam lui parle -- et `semantic_errors` compare
        # l'échéance d'un contrôle daté à ce champ, donc une heure fausse désarme un
        # garde-fou. La seule horloge fiable de la chaîne est celle qui écrit.
        current["computed_at"] = now_stamp()
        # Le texte qui ENTRE est jugé avant le plan qui le contient : la révision est
        # réinjectée au coach à chaque tour prescriptif, et title/note partent sur le
        # téléphone de Sam. Jugé ici et pas dans `validate` parce que l'historique
        # est append-only : la même règle appliquée au plan entier refuserait la
        # révision du jour à cause du texte de la veille.
        previous = existing["revisions"][-1] if existing else None
        fautes = [
            *intention_errors(current, f"{week} révision {current['revision']}"),
            *written_text_errors(current, f"{week} révision {current['revision']}"),
            *coherence_errors(week, previous, current),
            *change_evidence_errors(previous, current),
            *typed_target_errors(current, f"{week} révision {current['revision']}"),
        ]
        if fautes:
            raise SchemaError(fautes)
        candidate = {"week": week, "revisions": [*(existing or {}).get("revisions", []), current]}
        validate(candidate)
        # ⭐ La relecture part sur stdout DANS LES DEUX CHEMINS (2026-08-25). Le tour
        # Messenger passe par `stage_command` : c'est précisément celui du coach, donc
        # celui qui doit relire. Ne la rendre qu'au chemin CLI aurait laissé la
        # production sans son garde-fou et l'aurait donnée au seul usage manuel.
        turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
        if command == "preview":
            store.preview_command(commands.PLAN_REVISE,
                                  {"week": week, "revision": current}, turn_id=turn_id)
            print("SIMULATION VALIDÉE — aucun changement préparé ou enregistré.")
            print(revision_readback(week, previous, current))
            notes = _distance_notes(store, current)
            if notes:
                print(notes)
            return EXIT_OK
        if turn_id:
            store.stage_command(turn_id, f"plan:{week}:r{current['revision']}",
                                commands.PLAN_REVISE,
                                {"week": week, "revision": current})
            print("CHANGEMENT PRÉPARÉ — application en fin de tour encore requise.")
            print(revision_readback(week, previous, current))
            notes = _distance_notes(store, current)
            if notes:
                print(notes)
            return EXIT_OK
        saved = store.append_plan_revision(week, current, actor="week-plan-cli")
        print(revision_readback(week, previous, active(saved)))
        notes = _distance_notes(store, current)
        if notes:
            print(notes)
        return EXIT_OK
    plan = store.load_plan(week)
    if plan is None:
        if optional:
            print(f"{week} : aucun programme")
            return EXIT_OK
        raise NoteError(f"{week} : programme hebdomadaire absent dans PostgreSQL")
    validate(plan)
    revision = active(plan)
    stale = store.plan_needs_arbitration(week, revision["revision"])
    gates = gate_reasons(plan)
    if command == "show":
        print(as_json(plan))
    else:
        print(f"{week} : révision {revision['revision']} valide, "
              f"{len(revision['sessions'])} entrée(s)")
    if gates:
        print("\n  - ".join([f"{week} : en attente de la réponse de Sam :", *gates]),
              file=sys.stderr)
        return EXIT_AWAITING_GATE
    if stale:
        # Le programme est INTACT : il ne décrit simplement plus la semaine. Il s'imprime
        # donc, comme pour un contrôle daté, et seule la prescription attend.
        # Jusqu'au 2026-08-14 la péremption levait une NoteError AVANT l'impression :
        # code 1, stdout vide, donc un coach qui lance `show` conclut que son programme
        # est cassé alors qu'il l'a sous les yeux dans son prompt. La CLI disait
        # « invalide » pendant que le service disait « périmé, tu peux répondre ».
        print(f"{week} : arbitrage obligatoire, activité non rattachée apparue depuis "
              "la révision — le programme reste lisible, mais rien ne se prescrit "
              "avant une révision",
              file=sys.stderr)
        return EXIT_STALE
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "show", "revise", "preview"))
    parser.add_argument("--week", default=current_week())
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--input", help="JSON de revise/preview (défaut : stdin hors tour)")
    args = parser.parse_args()
    try:
        return _database_command(_database_store(), args.command, args.week, args.optional,
                                 args.input)
    except (NoteError, SchemaError, StorageError, json.JSONDecodeError,
            agent_input.AgentInputError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
