"""Le JSON qu'un tour du coach remet à une CLI : un fichier nommé, jamais une redirection.

Le 11/10, Claude Code 2.1.287 a refusé en `dontAsk` toute commande
`… < /tmp/x.json` : une redirection d'entrée lit un fichier, et un fichier hors du
dossier de travail n'est plus couvert par `Bash(/usr/local/bin/redline-agent-cli *)`.
Le bilan W41 a tourné trois fois sur Opus sans pouvoir écrire W42. La 2.1.197 laissait
passer la même commande : la forme dépendait de l'analyse que le binaire fait du shell.

D'où deux choix. La commande reste une commande nue (`--input <chemin>`), sans `<`,
`|` ni `;` : c'est la forme la moins exposée aux changements de cette analyse. Et le
fichier vit dans le dossier de travail, sous un nom propre au tour : deux tours
simultanés ne s'écrasent plus, et un `Write` raté ne fait plus relire le JSON d'un
autre tour. Le JSON du 11/10 traînait encore dans `/tmp` le lendemain.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from redline.model import paths
from redline.model.errors import InputError

#: Ignoré par Git (`.gitignore`) : le service fait `git add -A` en fin de tour.
DIRECTORY = ".agent-tmp"

_SUFFIX = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class AgentInputError(InputError):
    """Le fichier nommé n'est pas celui que ce tour a le droit de remettre."""


def turn_path(turn_id: str, suffix: str = "") -> str:
    """Le chemin, relatif au vault, que le service annonce au tour."""
    if suffix and not _SUFFIX.match(suffix):
        raise AgentInputError(f"suffixe de fichier invalide : {suffix!r}")
    return f"{DIRECTORY}/{turn_id}{'-' + suffix if suffix else ''}.json"


def resolve(path: str, *, root: Path | None = None, turn_id: str | None = None) -> Path:
    """Le fichier à lire, refusé s'il n'appartient pas au tour en cours.

    Hors tour (lancements opérateur, tests), tout chemin lisible convient :
    l'opérateur sait ce qu'il passe. Dans un tour, seul `.agent-tmp/<turn_id>[-<suffixe>].json`.
    """
    root = root or paths.vault_root()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip() if turn_id is None else turn_id
    if not turn_id:
        return candidate
    name = candidate.name
    stem = name[:-len(".json")] if name.endswith(".json") else ""
    suffix = stem[len(turn_id) + 1:] if stem.startswith(f"{turn_id}-") else None
    if (candidate.parent != (root / DIRECTORY).resolve()
            or not (stem == turn_id or (suffix and _SUFFIX.match(suffix)))):
        # Le refus dit comment réussir : le 27/10, le coach a tenté `cp`, refusé en
        # `dontAsk`, puis a abandonné une révision que l'athlète avait autorisée. `Write`
        # est toujours permis ; c'est le seul geste à faire.
        raise AgentInputError(
            f"--input : ce tour ne lit que {turn_path(turn_id)} "
            f"(ou {DIRECTORY}/{turn_id}-<nom>.json), reçu {path}. Réécris le même JSON "
            f"avec Write dans {turn_path(turn_id)}, puis relance la même commande avec "
            f"--input {turn_path(turn_id)}")
    return candidate


def read_text(path: str | None) -> str:
    """Le contenu remis : le fichier `--input`, ou stdin hors tour."""
    if path is None:
        return sys.stdin.read()
    target = resolve(path)
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AgentInputError(f"--input : fichier absent ({path}) ; écris-le avec Write "
                              "avant d'appeler la CLI") from None


def read_json(path: str | None) -> Any:
    """Le JSON remis ; `json.JSONDecodeError` si illisible."""
    return json.loads(read_text(path))


def clear(turn_id: str, *, root: Path | None = None) -> None:
    """Supprime les fichiers d'un tour, quelle qu'en soit l'issue."""
    directory = (root or paths.vault_root()) / DIRECTORY
    if not turn_id or not directory.is_dir():
        return
    for path in directory.glob(f"{turn_id}*.json"):
        path.unlink(missing_ok=True)
