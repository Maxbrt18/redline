"""Le prénom de l'athlète n'est écrit nulle part dans le moteur : il se rend à la sortie.

Les textes du service (prompts, notes de tour, consignes) portent le jeton
`⟨athlète⟩` à la place du prénom. Il est remplacé par `[athlete] name` de
`redline.toml` au moment où le texte quitte le processus : vers le modèle (les
adaptateurs Claude et Codex) et vers Messenger. Un texte qui sortirait sans passer par
ici montrerait le jeton tel quel, ce qui se voit ; un prénom écrit en dur, lui, se
serait glissé en silence chez un autre athlète.

Le français élide devant une voyelle : « de ⟨athlète⟩ » devient « d'Alex » mais reste
« de Marie ». Le rendu s'en charge, pour que les gabarits s'écrivent une seule fois.
"""

from __future__ import annotations

import re

TOKEN = "⟨athlète⟩"

# « de », « que », « jusque » devant un prénom qui commence par une voyelle ou un h muet.
_ELISION = re.compile(r"\b([Dd]e|[Qq]ue|[Jj]usque) " + re.escape(TOKEN))
_VOWEL = re.compile(r"^[AEIOUYÂÊÎÔÛÉÈÀHaeiouyâêîôûéèàh]")


def render(text: str, name: str) -> str:
    """Le texte avec le prénom à la place du jeton, élision comprise."""
    if TOKEN not in text:
        return text
    if _VOWEL.match(name):
        text = _ELISION.sub(lambda match: f"{match.group(1)[:-1]}'{name}", text)
    return text.replace(TOKEN, name)
