"""Ce que le coach relit de ses propres messages : le fond, sans ses tics.

Audit du 2026-10-11. Le service réinjecte au coach ses derniers messages à chaque tour
(souvenir relationnel, résumés des messages proactifs, recherche dans la conversation).
Il les relit comme des exemples et recopie leurs marqueurs : du 03/10 au 09/10, aucun
de ses 47 messages ne portait 🙂 ; dès que l'un en a porté un, 12 des 15 suivants
aussi, réprimande et consigne d'arrêt comprises, et le tic a traversé une session neuve
et un prompt de style changé. Un emoji de sa part dans les huit derniers messages
rendait le suivant 22 fois plus probable ; un emoji de l'athlète, 3 fois.

La relecture garde donc ce qui a été dit, et retire l'emoji et la formule d'ouverture,
qui ne portent aucune information. Le texte stocké reste intact : seule la copie que le
coach relit est épurée. Les messages de l'athlète ne passent jamais par ici.
"""

from __future__ import annotations

import re

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
OPENING = re.compile(
    r"^(?:salut|hello|hey|coucou|bonjour|bonsoir|yo)(?:\s+sam)?\s*[,!.]?\s+",
    re.IGNORECASE)
_SPACES = re.compile(r"[ \t]{2,}")


def muted(text: str) -> str:
    """Le message du coach tel qu'il se relit : sans emoji ni formule d'ouverture."""
    plain = OPENING.sub("", EMOJI.sub("", text).strip(), count=1)
    plain = _SPACES.sub(" ", plain).replace(" .", ".").replace(" ,", ",").strip()
    return plain[:1].upper() + plain[1:] if plain else "(réaction sans texte)"
