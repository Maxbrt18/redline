"""Sentinelles de propagation : un énoncé scientifique fragile ne voyage jamais nu.

Les notes de concept gradent leurs énoncés (`[Probable]`, marqueur de transposition),
mais chaque descente vers la couche opérationnelle (CLAUDE.md, hot-cache, profil, plan,
protocoles) rabote le grade : la paraphrase garde le fait et jette la réserve.

Ce module est la DONNÉE du contrôle `check_claim_propagation` de `vault-check.py` :
pour chaque sentinelle, un motif qui reconnaît l'énoncé dans la prose aval, et un motif
de marqueur qui doit apparaître dans le MÊME paragraphe. Conformément à « aucun code ne
lit de la prose pour en tirer un fait » : le code ne DÉRIVE rien de la prose, il la
VÉRIFIE contre ce registre.

Limites assumées : un grep attrape les formulations canoniques, pas les paraphrases
libres. C'est un fil de détente, pas une preuve.

Règle de maintenance : promouvoir un nouvel énoncé gradé vers un fichier aval = ajouter
sa sentinelle ici, dans le même commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import vault_layout

# Les fichiers AVAL : ceux qui résument sans être propriétaires de la science.
# Les notes de concept et les notes-source n'y figurent pas — chez le propriétaire,
# l'énoncé vit avec son grade complet, c'est sa raison d'être.
DOWNSTREAM = ("CLAUDE.md", *(str(path) for path in (
    vault_layout.HOT_CACHE, vault_layout.ATHLETE_PROFILE, vault_layout.CURRENT_PLAN,
    vault_layout.PROTOCOLS)))


@dataclass(frozen=True)
class Sentinel:
    name: str          # slug court, affiché dans le rapport
    claim: re.Pattern  # comment l'énoncé se reconnaît dans la prose aval
    marker: re.Pattern # le grade compact exigé dans le même paragraphe
    owner: str         # où vit la version complète (pour le message d'erreur)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


SENTINELS = (
    # Exemple à remplacer par les énoncés fragiles de votre propre corpus : un énoncé
    # observationnel ne doit pas circuler comme une loi.
    Sentinel(
        "exemple : cadence protectrice",
        _rx(r"cadence[^\n]{0,15}?protec|protec\w*[^\n]{0,15}?cadence"),
        _rx(r"associ|observationnel|probable|extrapol|pari"),
        "concepts/cadence.md",
    ),
)
