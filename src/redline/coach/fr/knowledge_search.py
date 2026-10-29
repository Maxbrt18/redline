"""Searching the vault notes, which are written in French."""

from __future__ import annotations

#: Words too common in a French query to say anything about its topic.
STOPWORDS = frozenset({"avec", "dans", "pour", "plus", "moins", "comment", "quelle",
                       "quelles", "quel", "quels", "leurs", "notre", "cette", "sont",
                       "avoir", "faire", "entre", "chez", "depuis", "apres", "avant"})

NOTHING_FOUND = (
    "Aucune fiche trouvée ; essaie d'autres termes. Cela ne prouve aucune absence de "
    "source.")

SPOTTING_ONLY = "Repérage seulement : ouvre et lis la fiche avant de citer un énoncé ou un chiffre."
