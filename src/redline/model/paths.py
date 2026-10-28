"""Les deux racines : le MOTEUR (ce dépôt) et le VAULT (les notes d'une instance).

Elles ont longtemps été le même dossier, et le code dérivait l'emplacement des notes
de celui de ses propres fichiers (`Path(__file__).parent.parent`). C'est ce qui
empêchait d'installer le moteur ailleurs que dans le vault qu'il sert.

La règle, désormais :
- le code, les gabarits et les fixtures se trouvent depuis `ENGINE_ROOT` ;
- tout ce qu'une instance écrit ou lit comme note se trouve depuis `vault_root()`.

`VAULT_PATH` est la variable que le service lit déjà (`redline.service.config`) : une seule
variable pour les deux, pour qu'un script lancé par le coach et le service voient le
même vault. Absente, le vault est le dépôt du moteur lui-même -- le déploiement
historique en un seul dossier continue de marcher sans configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The checkout: an editable install keeps the package inside it.
ENGINE_ROOT = Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    """Where a running service keeps what it writes outside PostgreSQL: the outbox of
    cards, the attachments being read, the Strava token cache. `REDLINE_STATE_DIR`, by
    default `var/` in the checkout; read at the call, like the vault."""
    raw = os.environ.get("REDLINE_STATE_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else ENGINE_ROOT / "var"


def outbox_dir() -> Path:
    """A PNG written here during a turn goes out to the athlete after the text."""
    return state_dir() / "outbox"


def vault_root() -> Path:
    """Le vault de l'instance, lu à l'appel (le service charge son .env après import)."""
    raw = os.environ.get("VAULT_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else ENGINE_ROOT
