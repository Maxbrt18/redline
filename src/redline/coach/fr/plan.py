"""The weekly plan as the coach and the athlete read it: journal, hot-cache, readback."""

from __future__ import annotations

#: A due precondition, as served to the coach before it prescribes.
GATE_REASON = (
    "contrôle daté du {due} non levé — DEMANDE À L'ATHLÈTE AVANT TOUTE PRESCRIPTION : "
    "{question} ; à défaut : {on_fail}. Le contrôle ne se lève qu'en enregistrant une "
    "révision dont la raison porte SA réponse, jamais en le retirant du programme.")

DISTANCE_TARGET_AND_CAP = "cible {target} km, plafond {cap} km"
DISTANCE_CAP = "plafond {cap} km"
DISTANCE_TARGET = "cible {target} km"
DURATION_CAP = "plafond {minutes} min"
RPE = "RPE {low}-{high}"
CADENCE = "cadence ~{cadence}{unit}"
CADENCE_UNIT = " pas/min"
CARBS = "glucides {grams} g/h"

DONE = "fait"
WITH_TRANSITION = ", transition {label}"
SESSION_LINE = "- **{day} {date}** : {title} ({status})"

JOURNAL_TITLE = "## Programme actif — révision {revision}"
JOURNAL_COMPUTED = "Calculé le `{computed_at}` · Motif : {reason}"
VOLUME_TARGET = "Volume couru visé : ~{km} km"
#: The journal must not claim that every gate holds the program: an observation is asked
#: and holds nothing.
GATE_SECTIONS = {
    "before": ("**Pré-conditions** (aucune séance retenue ne se prescrit tant qu'une "
               "révision ne porte pas la réponse) :"),
    "after": "**Observations** (à poser le jour dit ; elles ne retiennent aucune séance) :",
}
GATE_LINE = "- **{day}/{month}** : {question} → à défaut, {on_fail}"
ATTENTION = "**Points d'attention** : "
GENERATED = "Ce bloc est généré depuis le programme typé canonique. Ne pas l'éditer à la main."

JOURNAL_SKELETON = """\
# Bilan {week} ({span})

> Les séances restent dans le registre. Le programme opérationnel est la révision PostgreSQL
> `{week}` ; ce journal recevra la lecture de fin de semaine.

{block}

## Intention d'ouverture

## Ressenti de l'athlète

## Bilan de clôture

## Lecture du coach
"""

HOT_CACHE = ("- **Semaine en cours — {week} · plan typé r{revision}** : {sessions}. "
             "PostgreSQL → `athlete/journal/{file}.md`. Macro-plan jamais prescriptible.")
HOT_CACHE_CAP = " ≤{cap} km"

#: One line per session a revision touched.
DIFF = {
    "added": "{session} : AJOUTÉE le {day} ({title})",
    "moved": "{session} : DÉPLACÉE du {old_day} au {day}",
    "cancelled": "{session} : ANNULÉE le {day} ({title})",
    "modified": "{session} : MODIFIÉE le {day} ({title})",
    "removed": "{session} : RETIRÉE de la révision (elle était le {day})",
}
NO_SESSION = "AUCUNE SÉANCE"
WEEK_DAY = "{day} : {sessions}"
SESSION_STATUS = " ({status})"
FIRST_REVISION = "rien (première révision)"
NOTHING_CHANGED = ("AUCUNE séance ajoutée, annulée, déplacée ni modifiée. Si le message de "
                   "l'athlète demandait un changement, cette révision ne le porte pas.")
READBACK_SAVED = "{week} révision {revision} enregistrée et validée."
READBACK_CHANGES = "Ce qui change depuis {since} :"
READBACK_WEEK = "RELIS LA SEMAINE AVANT DE RÉPONDRE. Elle est maintenant :"
READBACK_VOLUME = "- Volume couru visé : ~{km} km"
READBACK_ATTENTION = "ATTENTION :"
READBACK_CHECK = ("Chaque fait du message de l'athlète doit se retrouver ci-dessus. Un jour "
                  "marqué AUCUNE SÉANCE est un oubli tant que tu n'as pas vérifié qu'il est "
                  "voulu.")
ORPHAN_DAY = ("{day} ne porte plus que des annulations ({titles}) et aucune séance à la "
              "place. Voulu, ou remplaçante oubliée ?")

GAP_UNREADABLE = "Écart aux {days} jours : lecture du registre impossible ({name})."

GAP_TITLE = "Écart aux {days} jours (règle n8, informatif, jamais bloquant) :"

PREVIEW_VALID = "SIMULATION VALIDÉE — aucun changement préparé ou enregistré."

CHANGE_STAGED = "CHANGEMENT PRÉPARÉ — application en fin de tour encore requise."

NO_PLAN = "{week} : aucun programme"

PLAN_VALID = "{week} : révision {revision} valide, {sessions} entrée(s)"

AWAITING_ATHLETE = "{week} : en attente de la réponse de l'athlète :"

ARBITRATION_REQUIRED = (
    "{week} : arbitrage obligatoire, activité non rattachée apparue depuis la révision — "
    "le programme reste lisible, mais rien ne se prescrit avant une révision")

# The plan as Messenger shows it to the athlete.
MESSENGER_NOTHING = "rien de prévu"
MESSENGER_DONE = " (fait)"
MESSENGER_AT = " à {time}"
MESSENGER_PLACE = "lieu "
MESSENGER_DAY = "{weekday} {day:%d/%m}"
MESSENGER_NOTHING_UNTIL_SUNDAY = "Rien de prévu d'ici dimanche."
MESSENGER_NOTHING_TODAY = "Rien de prévu aujourd'hui."
MESSENGER_NEXT = "Prochaine séance, {day} : "
MESSENGER_CANCELLED = "{day} : {title} annulé"
MESSENGER_MOVED = "{title} déplacé de {before} à {after} : "
MESSENGER_REMOVED = "{day} : {title} retiré"
MESSENGER_ONE_CHANGE = "Changement au programme : {change}."
MESSENGER_CHANGES = "Changements au programme :"
MESSENGER_TODAY = "Aujourd'hui : "
MESSENGER_TODAY_HELD = "Aujourd'hui : {titles}, en attente de ta réponse."
MESSENGER_REST_OF_WEEK = "La suite de la semaine :"
MESSENGER_WEEK = "Programme du {first:%d/%m} au {last:%d/%m} :"

# What the coach reads in place of a plan that cannot be served.
CANONICAL_MISSING = "ABSENT OU INVALIDE"
ACTIVE_MISSING = ("ABSENTE OU INVALIDE : ne prescris aucune séance. Une réparation dédiée est "
                  "programmée par le service ; ne la fais pas ici")
