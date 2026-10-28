"""Le coût de lecture du coach : un plafond PAR FICHIER, dans un ÉTAGE de lecture.

Ici et pas dans `scripts/vault-check.py`, cf. `strength_tables.py`.

**Par fichier et pas en cagnotte commune** (2026-07-27) : les fichiers budgétés n'ont pas
la même volatilité — `hot-cache.md` est réécrit à chaque session, `athlete.md` presque
jamais. En cagnotte, une phrase ajoutée aux instructions faisait échouer une mise à jour
du cache : l'alerte désignait toujours le mauvais coupable.

**Deux étages et pas un seul total** (2026-08-06) : ce fichier s'appelait « budget de
lecture au **démarrage** » et additionnait trois fichiers comme s'ils étaient payés
ensemble. Ce n'est plus vrai depuis le 2026-08-05 : l'échelle de lecture de `CLAUDE.md` a
déplacé `athlete.md` en **N2**, lu quand un jugement s'engage — pas à chaque message. Le
total de 6300 décrivait donc un coût que personne ne payait jamais, et il masquait le seul
chiffre qui compte au quotidien. Les deux étages ne se compensent pas : dégraisser
`athlete.md` ne rend pas un mot au coût par tour, et c'est précisément ce que la
séparation rend visible.

Le total d'un étage est sa vraie contrainte ; les plafonds ne font que la rendre
diagnosticable. Relever l'un exige de rouvrir l'autre.

**Étage « par tour » rouvert 3500 -> 3600, `CLAUDE.md` 2100 -> 2200 (2026-08-15).** Le
plafond de 2100 datait du 2026-08-05, le jour où la doctrine de maintenance a quitté
`CLAUDE.md` — il chiffrait une intention, pas un contenu mesuré. Ce qui a tranché n'est
pas le dépassement (41 mots) mais **d'où il venait** : le dernier commit à toucher le
fichier s'appelle « comprime CLAUDE.md sans lui retirer de règle » et atterrit quand même
à 2141. Un fichier déjà comprimé qui reste 2 % au-dessus ne dit pas qu'il a dérivé, il dit
que le plafond a été posé trop bas. On rouvre donc, une fois, de +100 — pas de la marge
confortable, juste de quoi cesser de mentir sur la cible.

⚠ **Le prochain dépassement est une coupe, pas une seconde hausse.** Cette réouverture ne
crée pas de précédent : elle corrige un plafond mal calibré à sa pose. Un plafond qu'on
relève deux fois n'est plus un frein, c'est un compteur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import InputError

# Ce qu'on paie, et à quelle fréquence.
PER_TURN = "par tour"   # à CHAQUE message, y compris « ça va ? »
N2 = "N2"               # seulement quand un jugement s'engage (hausse, douleur, renfo)

TOTALS = {
    PER_TURN: 3600,
    N2: 2800,
}

# Plafond par rôle, pas par taille constatée. La marge du hot-cache est la plus large en
# proportion : c'est le seul qui doit absorber une semaine nouvelle sans qu'on ait à
# tailler ailleurs le jour où on l'écrit.
CEILINGS = {
    "CLAUDE.md": 2200,           # comment RÉPONDRE — la maintenance est dans docs/
    "ai/hot-cache.md": 1400,     # cache volatil — réécrit à chaque session, doit respirer
    "athlete/athlete.md": 2800,  # profil — bouge à la blessure ou au record, pas à la séance
}

TIER_OF = {
    "CLAUDE.md": PER_TURN,
    "ai/hot-cache.md": PER_TURN,
    "athlete/athlete.md": N2,
}

FILES = tuple(CEILINGS)


class BudgetError(InputError):
    """Configuration incohérente : dans un étage, la somme des plafonds dépasse le total."""


@dataclass
class Audit:
    counts: dict = field(default_factory=dict)
    over: list = field(default_factory=list)   # [(fichier, mots, plafond)]

    @property
    def totals(self) -> dict:
        """Le coût réel de chaque étage."""
        out = dict.fromkeys(TOTALS, 0)
        for name, words in self.counts.items():
            out[TIER_OF[name]] += words
        return out

    @property
    def over_total(self) -> list:
        """[(étage, mots, plafond)] pour chaque étage en dépassement."""
        return [(tier, words, TOTALS[tier])
                for tier, words in self.totals.items() if words > TOTALS[tier]]

    @property
    def near_total(self) -> bool:
        """Un étage sous son plafond, mais à moins de 5 % de marge."""
        return not self.over and not self.over_total and any(
            words > TOTALS[tier] * 0.95 for tier, words in self.totals.items())

    @property
    def detail(self) -> str:
        by_tier = []
        for tier, total in self.totals.items():
            files = " · ".join(f"{name} {self.counts[name]}/{CEILINGS[name]}"
                               for name in FILES
                               if TIER_OF[name] == tier and name in self.counts)
            by_tier.append(f"[{tier} {total}/{TOTALS[tier]}] {files}")
        return "  ".join(by_tier)

    def problems(self) -> list:
        """Chaque dépassement, nommé — l'étage dit s'il se paie à chaque message."""
        lines = [f"{name} : {words} mots > {ceiling} — étage « {TIER_OF[name]} »"
                 for name, words, ceiling in self.over]
        lines += [f"étage « {tier} » : {words} mots > {total}"
                  for tier, words, total in self.over_total]
        return lines


def words(text: str) -> int:
    return len(text.split())


def audit(texts: dict) -> Audit:
    """`{chemin: contenu}` -> l'audit. Un fichier absent compte pour 0."""
    for tier, total in TOTALS.items():
        ceilings = sum(c for name, c in CEILINGS.items() if TIER_OF[name] == tier)
        if ceilings > total:
            raise BudgetError(
                f"étage « {tier} » : somme des plafonds {ceilings} > total {total} — "
                "relever un plafond exige de rouvrir le total, pas de le contourner")
    result = Audit()
    for name in FILES:
        result.counts[name] = words(texts.get(name) or "")
        if result.counts[name] > CEILINGS[name]:
            result.over.append((name, result.counts[name], CEILINGS[name]))
    return result
