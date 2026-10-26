#!/usr/bin/env python3
"""Complète une mesure pending par identité stable, sans éditer une projection.

`--fc-douteuse sans_brassard|contexte --cause "<ses mots>"` déclare douteuse une FC
moyenne déjà connue (2026-10-20) : sans brassard elle ne compte plus nulle part ;
en contexte (repas, maladie) elle sort des comparaisons de FC à allure, donc de la porte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import commands  # noqa: E402

METRICS = ("rpe", "pain", "enjoyment", "distance_km", "duration_s", "pace_s_per_km",
           "hr_avg_bpm", "hr_max_bpm", "cadence_spm", "elevation_gain_m",
           "elevation_loss_m", "wbgt_c")


def resolve_key(store, key: str, date: str, kind: str) -> str:
    """La clé de l'observation visée, par identité stable OU par sa date.

    `--key` seul exigeait un identifiant que rien ne publiait : inventé au moment du
    `ledger-add`, il ne survivait qu'à la mémoire de la session. Le 2026-08-14 cette
    mémoire a été effacée entre-temps, le coach a deviné `strava:<id>` — logique, il
    avait l'id sous les yeux — et la ligne portait `messenger:2026-08-13:...`. Une
    date, elle, se lit dans le registre et se dit en français.
    """
    if key:
        return key
    candidats = [row for row in store.observation_rows()
                 if row["occurred_on"].isoformat() == date
                 and (not kind or row["kind"] == kind)]
    if not candidats:
        raise SystemExit(f"aucune observation le {date}"
                         + (f" de type {kind}" if kind else "")
                         + " — vérifie avec `fact-read.py observations`")
    if len(candidats) > 1:
        details = " · ".join(f"{row['kind']} (clé={row['idempotency_key']})"
                             for row in candidats)
        raise SystemExit(f"{len(candidats)} observations le {date} : {details}. "
                         "Précise --type, ou passe --key.")
    return candidats[0]["idempotency_key"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="",
                        help="clé de l'observation (cf. `fact-read.py observations`)")
    parser.add_argument("--date", default="",
                        help="AAAA-MM-JJ — alternative à --key, résolue sur le registre")
    parser.add_argument("--type", dest="kind", default="",
                        help="lève l'ambiguïté quand une date porte plusieurs lignes")
    parser.add_argument("--metric", choices=METRICS)
    parser.add_argument("--fc-douteuse", dest="hr_doubt", choices=("sans_brassard", "contexte"),
                        help="déclare douteuse la FC moyenne connue de l'observation")
    parser.add_argument("--cause", default="", help="la cause déclarée par Sam, ses mots")
    values = parser.add_mutually_exclusive_group()
    values.add_argument("--value", type=float)
    values.add_argument("--unavailable", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if not args.database_url:
        parser.error("DATABASE_URL obligatoire")
    from scripts.storage import Database, OperationalStore, StorageError

    if not args.key and not args.date:
        parser.error("--key ou --date obligatoire (cf. `fact-read.py observations`)")
    if args.hr_doubt:
        if args.metric or args.value is not None or args.unavailable:
            parser.error("--fc-douteuse se passe seul, sans --metric ni valeur")
        if not args.cause.strip():
            parser.error("--fc-douteuse exige --cause : la raison donnée par Sam")
    elif not args.metric or (args.value is None and not args.unavailable):
        parser.error("--metric et (--value ou --unavailable) obligatoires")

    store = OperationalStore(Database(args.database_url, application_name="ledger-complete"))
    # Résolu AVANT la mise en scène : une commande de tour préparée sur une clé
    # inexistante n échoue qu à la validation, donc bien après la réponse du coach.
    key = resolve_key(store, args.key, args.date, args.kind)
    # Vérifiée MAINTENANT, pas à la frontière du tour. Une commande mise en scène sur
    # une clé inexistante ne se révèle qu'à la validation, c'est-à-dire une fois la
    # réponse du coach écrite : il ne peut plus se corriger, et tout le tour tombe.
    # Ici il lit l'erreur, relance avec la bonne clé, et son message part intact.
    observation_id = store.observation_id(key)
    if observation_id is None:
        recents = " · ".join(
            f"{row['occurred_on'].isoformat()} {row['kind']} (clé={row['idempotency_key']})"
            for row in store.observation_rows()[:5])
        raise SystemExit(f"observation inconnue : {key}. Récentes — {recents}")
    if args.hr_doubt:
        payload = {"idempotency_key": key, "reason": args.hr_doubt, "note": args.cause.strip()}
        if turn_id:
            digest = hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
            store.stage_command(turn_id, f"hr-doubt:{key}:{digest}", commands.HR_DOUBT,
                                payload)
            print(f"{key} : FC douteuse ({args.hr_doubt}) préparée pour le tour.")
            return 0
        try:
            store.doubt_hr(observation_id, args.hr_doubt, args.cause, actor="ledger-complete")
        except StorageError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{key} : FC moyenne déclarée douteuse ({args.hr_doubt}).")
        return 0
    if turn_id:
        payload = {"idempotency_key": key, "metric": args.metric,
                   "value": args.value, "unavailable": args.unavailable}
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        store.stage_command(turn_id, f"measurement:{key}:{args.metric}:{digest}",
                            commands.MEASUREMENT_COMPLETE, payload)
        print(f"{key} : {args.metric} préparée pour le tour.")
        return 0
    try:
        store.complete_measurement(observation_id, args.metric, args.value,
                                   unavailable=args.unavailable, actor="ledger-complete")
    except StorageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{key} : {args.metric} complétée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
