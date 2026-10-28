"""Comment chaque correction de l'athlète se rejoue, lu dans un registre typé.

`coaching/corrections.md` raconte l'erreur et nomme son porteur ; `vault-check.py`
(« règles de méta portées ») vérifie qu'un porteur est NOMMÉ, jamais qu'il porte. Pour une
règle de code, un test la fige. Pour une règle de rédaction, portée par un prompt ou
CLAUDE.md, seul un scénario rejoué par `server/evaluate-coaching.py` peut montrer qu'elle
tient encore. Au 2026-10-04, 6 des 51 corrections avaient un scénario.

Le registre `server/tests/fixtures/corrections-replay.json` donne à chaque correction un
mode de rejeu, choisi par un humain et jamais deviné depuis la prose :

- `scenario` : un ou plusieurs scénarios de `coaching-scenarios.json` la rejouent ;
- `code` : le correctif est déterministe et vit dans les fichiers nommés (`carriers`) ;
- `hors-banc` : rien ne se rejoue en conversation (contenu d'une note corrigé, règle de
  maintenance du vault, mécanisme retiré) ; `note` dit pourquoi ;
- `a-rejouer` : le correctif vit dans une consigne du modèle et n'a pas encore de
  scénario. C'est la dette que le banc doit résorber.

Ce module ne lit de `corrections.md` que l'identité des entrées (titre de section ou
première cellule de tableau), comme le contrôle des porteurs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import InputError

LEDGER = "server/tests/fixtures/corrections-replay.json"
SCENARIOS = "server/tests/fixtures/coaching-scenarios.json"
MODES = ("scenario", "code", "hors-banc", "a-rejouer")
#: Une correction nouvelle a deux bilans pour recevoir son mode de rejeu.
GRACE_DAYS = 14

HEADING = re.compile(r"^#{2,3} (\d{4}-\d{2}-\d{2}.*)$", re.MULTILINE)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ReplayFormatError(InputError):
    """Le registre ne respecte pas son schéma : on refuse au lieu d'interpréter."""


@dataclass(frozen=True)
class Correction:
    key: str

    @property
    def date(self) -> dt.date:
        found = DATE.search(self.key)
        assert found, self.key
        return dt.date.fromisoformat(found.group(0))


def entries(text: str) -> list[Correction]:
    """Les corrections du journal, identifiées par leur titre ou leur première cellule.

    Une ligne de tableau sans libellé après sa date se distingue par le début de la
    cellule suivante : plusieurs lignes historiques partagent la même date.
    """
    found = [Correction(match.group(1).strip()) for match in HEADING.finditer(text)]
    for line in text.splitlines():
        if not line.startswith("| 20"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        head = cells[0]
        if DATE.fullmatch(head) and len(cells) > 1:
            head = f"{head} · {' '.join(cells[1].replace('*', '').split()[:8])}"
        found.append(Correction(head))
    return found


def load_ledger(text: str) -> dict[str, dict]:
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise ReplayFormatError("le registre est une liste d'objets")
    ledger: dict[str, dict] = {}
    for row in rows:
        key, mode = row.get("correction"), row.get("replay")
        if not key or mode not in MODES:
            raise ReplayFormatError(f"entrée invalide : {row!r}")
        if key in ledger:
            raise ReplayFormatError(f"correction en double : {key}")
        if mode == "scenario" and not row.get("scenarios"):
            raise ReplayFormatError(f"{key} : `scenarios` manquant")
        if mode == "code" and not row.get("carriers"):
            raise ReplayFormatError(f"{key} : `carriers` manquant")
        if mode in ("hors-banc", "a-rejouer") and not row.get("note"):
            raise ReplayFormatError(f"{key} : `note` manquante")
        ledger[key] = row
    return ledger


@dataclass
class Audit:
    unregistered: list[Correction]
    late: list[Correction]
    orphans: list[str]
    broken: list[str]
    owed: list[str]


def audit(root: Path, corrections_text: str, today: dt.date) -> Audit:
    """`corrections_text` vient de l'appelant (le `Vault` de vault-check) ; le registre,
    les scénarios et les porteurs se lisent sous `root`."""
    corrections = entries(corrections_text)
    ledger = load_ledger((root / LEDGER).read_text(encoding="utf-8"))
    scenario_ids = {case["id"] for case in
                    json.loads((root / SCENARIOS).read_text(encoding="utf-8"))}
    keys = {correction.key for correction in corrections}
    unregistered = [c for c in corrections if c.key not in ledger]
    broken = []
    for key, row in ledger.items():
        broken += [f"{key} : scénario inconnu {name}" for name in row.get("scenarios", [])
                   if name not in scenario_ids]
        broken += [f"{key} : porteur absent {path}" for path in row.get("carriers", [])
                   if not (root / path).exists()]
    return Audit(
        unregistered=unregistered,
        late=[c for c in unregistered if (today - c.date).days > GRACE_DAYS],
        orphans=sorted(key for key in ledger if key not in keys),
        broken=broken,
        owed=[key for key, row in ledger.items() if row["replay"] == "a-rejouer"],
    )
