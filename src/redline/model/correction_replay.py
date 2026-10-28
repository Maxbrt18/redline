"""How each correction from the athlete is replayed, read from a typed registry.

`coaching/corrections.md` tells the error and names its carrier; `vault-check.py`
verifies that a carrier is NAMED, never that it carries. A code rule is frozen by a test.
A drafting rule, carried by a prompt or CLAUDE.md, can only be shown to still hold by a
scenario replayed by `redline.cli.evaluate_coaching`.

The vault registry (`vault_layout.CORRECTIONS_REPLAY`) gives each correction a replay
mode, chosen by a human and never guessed from prose:

- `scenario`: one or more scenarios of `coaching-scenarios.json` replay it;
- `code`: the fix is deterministic and lives in the named files (`carriers`);
- `hors-banc`: nothing replays in conversation (corrected note content, vault
  maintenance rule, removed mechanism); `note` says why;
- `a-rejouer`: the fix lives in a model instruction and has no scenario yet. This is the
  debt the bench must pay down.

This module reads only the identity of the entries from `corrections.md` (section title
or first table cell), like the carrier check.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths, vault_layout
from .errors import InputError

SCENARIOS = "src/redline/evaluation/coaching-scenarios.json"
MODES = ("scenario", "code", "hors-banc", "a-rejouer")
#: A new correction has this many days to receive its replay mode.
GRACE_DAYS = 14

HEADING = re.compile(r"^#{2,3} (\d{4}-\d{2}-\d{2}.*)$", re.MULTILINE)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ReplayFormatError(InputError):
    """The registry does not follow its schema: reject rather than interpret."""


@dataclass(frozen=True)
class Correction:
    key: str

    @property
    def date(self) -> dt.date:
        found = DATE.search(self.key)
        assert found, self.key
        return dt.date.fromisoformat(found.group(0))


def entries(text: str) -> list[Correction]:
    """The corrections of the journal, identified by their title or first cell.

    A table row with no label after its date is told apart by the start of the next
    cell: several rows can share the same date.
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


def audit(vault: Path, corrections_text: str, today: dt.date,
          engine: Path = paths.ENGINE_ROOT) -> Audit:
    """`corrections_text` comes from the caller (the `Vault` of vault-check). The registry
    is read in the vault, the scenarios in the engine; a carrier lives in either."""
    corrections = entries(corrections_text)
    ledger = load_ledger((vault / vault_layout.CORRECTIONS_REPLAY).read_text(encoding="utf-8"))
    scenario_ids = {case["id"] for case in
                    json.loads((engine / SCENARIOS).read_text(encoding="utf-8"))}
    keys = {correction.key for correction in corrections}
    unregistered = [c for c in corrections if c.key not in ledger]
    broken = []
    for key, row in ledger.items():
        broken += [f"{key} : scénario inconnu {name}" for name in row.get("scenarios", [])
                   if name not in scenario_ids]
        broken += [f"{key} : porteur absent {path}" for path in row.get("carriers", [])
                   if not (vault / path).exists() and not (engine / path).exists()]
    return Audit(
        unregistered=unregistered,
        late=[c for c in unregistered if (today - c.date).days > GRACE_DAYS],
        orphans=sorted(key for key in ledger if key not in keys),
        broken=broken,
        owed=[key for key, row in ledger.items() if row["replay"] == "a-rejouer"],
    )
