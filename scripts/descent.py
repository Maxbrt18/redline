#!/usr/bin/env python3
"""Le D− d'une activité, que Strava ne renvoie pas.

L'API donne le dénivelé POSITIF (`elevation_gain`) et rien pour le négatif. Or sur un
parcours vallonné la descente est une charge à part entière (impact, freinage) : la
colonne D− du registre doit donc être remplie, pas laissée vide.

Méthode — l'identité de bilan, pas une seconde estimation :

    D− = D+ + (altitude_départ − altitude_arrivée)

Ce que ça achète : le chiffre s'ancre sur le D+ de Strava, donc sur SON lissage, au
lieu d'en refaire un avec un lissage à nous qui ne collerait pas à la colonne D+ juste
à côté. Seuls les deux points extrêmes du flux d'altitude entrent dans le calcul.

Calibration du 2026-07-29, sur les 23 activités du registre : recalculer le D+ par
somme des deltas positifs du flux (résolution 100) retombe à quelques mètres de
celui de Strava sur les sorties roulantes (176 vs 175,3 · 77 vs 78,8 · 209 vs 212,3)
mais le sous-estime de ~5 % sur une grosse rando (971 vs 1022,5) : le lissage mange
les replats. L'identité ci-dessus n'a pas ce défaut. Contrôle indépendant : sur la
descente du Gotzenalm (2026-07-21), seule ligne dont le D− était déjà connu par
Strava, elle rend 1101 pour 1096 relevés — 0,5 % d'écart.

Usage :
    python3 scripts/descent.py --dplus 175.3 --altitude '[262.9, ..., 262.6]'
    python3 scripts/strava.py streams <id> --keys altitude \
        | python3 scripts/descent.py --dplus 175.3

Le flux passe tel quel : soit le JSON rendu par `strava.py streams`
({"altitude": [...]}), soit la liste seule.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import agent_input  # noqa: E402


def descent(dplus: float, altitude: list) -> int:
    """Le D− en mètres, arrondi. Le plat rend 0, jamais rien."""
    if len(altitude) < 2:
        raise ValueError("flux d'altitude trop court : deux points au minimum")
    return max(0, round(dplus + altitude[0] - altitude[-1]))


def _altitude(payload: str) -> list:
    data = json.loads(payload)
    if isinstance(data, dict):
        data = data.get("altitude")
    if not isinstance(data, list):
        raise ValueError("flux d'altitude introuvable : attendu une liste, ou un objet "
                         "{\"altitude\": [...]} tel que renvoyé par `strava.py streams`")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dplus", type=float, required=True,
                        help="elevation_gain de l'activité, en mètres (Strava)")
    parser.add_argument("--altitude", help="flux d'altitude JSON (défaut : stdin)")
    parser.add_argument("--input", help="fichier du flux d'altitude JSON")
    args = parser.parse_args()

    try:
        altitude = _altitude(args.altitude or agent_input.read_text(args.input))
        print(descent(args.dplus, altitude))
    except ValueError as exc:
        sys.exit(f"D− incalculable — {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
