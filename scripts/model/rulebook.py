"""Le règlement compilé : les garde-fous EN VIGUEUR, une ligne chacun, et leurs paramètres.

Né de l'audit du 2026-09-27. Les huit garde-fous vivaient dans la prose de
`coaching/current-plan.md`, entrelacés avec leur histoire (« ajouté le… », « corrigé le… »,
chiffres barrés). Le modèle devait retrouver la version en vigueur dans un palimpseste,
et `coaching/corrections.md` montre ce que ça coûte : un plafond de deux initiatives
ressuscité, un préalable « deux semaines silencieuses » retiré mais redit, une sortie
longue de 12-14 km revenue d'un plan périmé. Même forme à chaque fois : une version
ancienne qui remonte.

Ce module sépare les deux choses :

- **la règle en vigueur** (ici) : courte, paramétrée, gradée, avec son propriétaire ;
- **son raisonnement et son histoire** : restent chez le propriétaire nommé, et c'est
  là que la confrontation l'attaque.

Il est la SOURCE UNIQUE des paramètres que le code applique : `scripts/ledger.py`
(fenêtre de 30 jours, stagnation, jour exigeant) et `scripts/signals.py`. Changer un
seuil ici le change partout ; le changer ailleurs est impossible.

⚠ Changer le SENS d'une règle reste une décision de coaching : entrée datée dans
`coaching/decisions.md`, puis le propriétaire, puis cette ligne, dans le même commit.
Ce fichier ne décide rien ; il compile ce qui a été décidé.
"""

from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------- paramètres appliqués

#: Garde-fou n°8 : une transition est une séance dont la NATURE sort des 30 derniers
#: jours (1re qualité, 1er bloc descente, terrain neuf). La nocturne n'en est plus une
#: (2026-10-11, decisions.md) : la frontale ne change pas la charge osseuse d'un parcours connu. Une par semaine,
#: plafond écrit avant. Les fenêtres +3/+4 sont retirées le 2026-10-04 (decisions.md) :
#: une modélisation du délai que sa biographie ne démontre pas, et qui rendait la
#: préparation infaisable.
#:
#: ⭐ AUCUN POURCENTAGE FIXE DE PROGRESSION (décision de l'athlète, 2026-09-27). La
#: progression de la sortie longue se JUGE (tolérance observée, historique) ; le code
#: montre l'écart aux 30 jours, il ne le tranche pas. Source de l'ancien critère :
#: `episodes/src-frandsen-single-session.md`.
TRANSITION_LOOKBACK_DAYS = 30
#: Appliqué à l'écriture par `week-plan.py revise` sur le champ typé `transition`
#: (`scripts/model/transitions.py`).
TRANSITIONS_PER_WEEK = 1
#: La porte (2026-10-04) : une hausse facile s'ajoute si le pic d'effort hebdo reste à ce
#: niveau ou en dessous, sans tendance à la hausse sur autant de semaines, réveils à la
#: base. Remplace la checklist verte. La marche n'est jamais demandée (2026-10-11) : l'athlète
#: signale lui-même une gêne.
GATE_EFFORT_PEAK = 1.5
GATE_TREND_WEEKS = 2
#: Les premières fois que la course exige avant l'affûtage (current-plan.md § Vue macro :
#: reconnaissance du tracé ; plus de bloc descente dédié depuis le 2026-10-04,
#: plus de nocturne prescrite depuis le 2026-10-11, l'athlète en fait une à son initiative ;
#: le D- monte dans les sorties longues). Une première fois faite se
#: lit dans PostgreSQL : séance typée `transition` dont le cycle est terminé.
RACE_FIRSTS = ("reconnaissance",)
#: Garde-fou n°2, calendrier compilé de current-plan.md § Vue macro. Changer l'un
#: change l'autre, dans le même commit.
ASSIMILATION_WEEKS = ("2026-W35", "2026-W39", "2026-W43")
#: L'affûtage couvre les N dernières semaines, semaine de course incluse : aucune
#: nouveauté (race_countdown.py, jalon « taper »). W46-W48 pour une course en W48.
TAPER_WEEKS = 3
#: Garde-fou n°7 : est exigeant un jour de qualité, de sortie longue ou d'EF > 1 h 15.
HARD_MIN_HOURS = 1.25
#: Garde-fou n°3 : plafond de douleur injury d'effort, sur SA table.
PAIN_CAP = 2
#: Scénario 9 du pré-mortem : la plus longue sans nouveau record sur autant de semaines
#: CLOSES consécutives ouvre le protocole d'instruction des baisses ([[goals]]).
STALL_WEEKS = 3
#: En dessous de cet écart, deux sorties sont « la même » plus longue : bruit GPS et
#: arrondi de tracé (11,53 contre 11,5 n'est pas un progrès).
STALL_TOLERANCE_KM = 0.1


@dataclass(frozen=True)
class Rule:
    """Une règle en vigueur. `text` est ce que le coach applique ; le reste le situe."""

    id: str
    title: str
    text: str
    grade: str
    owner: str
    enforced_by: str

    def line(self) -> str:
        return f"{self.id} {self.title} : {self.text} ({self.grade})"


RULES: tuple[Rule, ...] = (
    Rule("n1", "Qualité",
         "monte avec la SL porte ouverte, sauf semaine d'introduction. Une par semaine, "
         "zéro VMA.",
         "décision du 2026-10-11",
         "coaching/current-plan.md § garde-fou n°1 ; coaching/decisions.md 2026-10-11",
         "le coach"),
    Rule("n2", "Assimilation",
         "-25 à -30 % toutes les 3-4 semaines, fréquence gardée ; on n'y ajoute rien.",
         "règle de coaching", "coaching/current-plan.md § garde-fou n°2",
         "le coach"),
    Rule("n3", "Douleur injury",
         f"plafond {PAIN_CAP}/10 à l'effort, sur sa table ; au-dessus ou foulée modifiée : "
         "arrêt. Persistante > 48 h : -30 %, puis paliers 50/70/85 % et 2 semaines sans "
         "qualité.",
         "règle de coaching", "coaching/current-plan.md § garde-fou n°3",
         "le coach"),
    Rule("n4", "Sommeil et FC",
         "2-3 mauvaises nuits consécutives ou FC anormalement haute en footing : "
         "qualité supprimée, semaine traitée en assimilation.",
         "règle de coaching", "coaching/current-plan.md § garde-fou n°4",
         "le coach ; FC à allure calculée par signals.py"),
    Rule("n5", "Pas de rattrapage",
         "une séance manquée ne se rattrape jamais.",
         "règle de coaching", "coaching/philosophy.md", "le coach"),
    Rule("n6", "Dernier intervalle",
         "une qualité finit dans sa bande RPE, une répétition en réserve ; un all-in "
         "final la rate. Allures comparées au sein de la séance seulement.",
         "attribution rétrospective, n = 2",
         "coaching/current-plan.md § garde-fou n°6 ; coaching/decisions.md 2026-10-26",
         "le coach"),
    Rule("n7", "Espacement",
         "jamais deux jours exigeants consécutifs (qualité, sortie longue, EF > 1 h 15). "
         "Une semaine à contrainte se réduit, elle ne se condense pas.",
         "règle de coaching", "coaching/current-plan.md § garde-fou n°7",
         "week-plan.py revise (champ demanding)"),
    Rule("n8", "Transition",
         f"séance dont la nature sort des {TRANSITION_LOOKBACK_DAYS} derniers jours "
         "(1re qualité, 1er bloc descente, terrain neuf). Une par semaine, "
         "sous-dosée, plafond écrit avant, sans fenêtre.",
         "pari",
         "coaching/current-plan.md § garde-fou n°8",
         "week-plan.py revise (champ transition) ; feasibility.py (à rebours)"),
    Rule("po", "Porte",
         "une hausse facile (palier de SL sans pourcentage fixe, EF allongée, sortie de plus) "
         f"s'ajoute si le pic d'effort hebdo reste au plus {str(GATE_EFFORT_PEAK).replace('.', ',')}/10 "
         f"sans hausse sur {GATE_TREND_WEEKS} semaines, réveils à la base ; "
         "elles se cumulent. Case rouge : semaine suivante figée ; deux : un cran en arrière.",
         "décision de l'athlète du 2026-10-04",
         "coaching/current-plan.md § La porte ; coaching/decisions.md 2026-09-27 et 2026-10-04",
         "le coach ; écart aux 30 jours affiché par week-plan.py et ledger.py"),
    Rule("de", "Descente",
         "le D- monte dans les SL, plafond écrit avant, descente relâchée. "
         "Les bouts W44-W45 : aucun drapeau rouge sur 7 jours (douleur focale, nocturne, "
         "antérieure, ou gênant la marche), effort au plus 1/10 sur 3 lignes ; J+1 à sa base. "
         "Drapeau rouge : suspension et avis médical.",
         "décision de l'athlète du 2026-09-27 ; les symptômes n'excluent pas une lésion",
         "coaching/workouts/descente.md ; coaching/decisions.md 2026-09-27 et 2026-10-04", "le coach"),
    Rule("s9", "Arrivée à vide",
         f"plus longue sortie sans nouveau record sur {STALL_WEEKS} semaines closes : "
         "le protocole d'instruction des baisses s'ouvre, sans attendre le jalon daté.",
         "règle de coaching", "coaching/pre-mortem-2026-11-29.md § scénario 9 ; athlete/goals.md",
         "ledger.py --semaine (bloc Trajectoire)"),
)



def render() -> str:
    """Le bloc injecté au coach : la règle en vigueur, jamais son histoire."""
    lines = ["[RÈGLEMENT EN VIGUEUR, invisible, ne pas réciter. Il fait foi sur toute "
             "formulation plus ancienne ; le raisonnement vit chez le propriétaire.]"]
    lines.extend("- " + rule.line() for rule in RULES)
    lines.append("[/RÈGLEMENT]")
    return "\n".join(lines)
