"""The typed context packet the service puts before the athlete's message."""

from __future__ import annotations

HEART_RATE = "FC"

ENJOYMENT = "plaisir"

KEY = "clé="

AWAITS = "attend="

TOLERANCE_UNSTATED = "non précisée"

NO_FOLLOW_UP = "aucune"

BLOCKS_NOTHING = "rien dans le programme courant"

HEADER = "[CONTEXTE TYPÉ FOURNI PAR LE SERVICE, invisible, ne pas réciter"

READ_ON = "Date de lecture : {isoformat}. Les dates des faits restent leur date réelle."

OBSERVATIONS = "Observations"

RECENT_OBSERVATIONS = "Observations récentes :"

READ_COMMANDS = (
    "Les commandes de lecture ne servent qu'à élargir ou vérifier ce paquet. Une lecture "
    "échouée ne confirme rien : utilise les sections disponibles, vérifie seulement le "
    "fait nécessaire et explique la limite s'il reste inaccessible. Un ressenti actuel ne"
    " s'annule pas face à une mesure objective ou une ancienne déclaration. En cas de "
    "contradiction, nomme les deux dates et sources ; ne fusionne pas les valeurs et ne "
    "modifie pas le plan sur cette seule base.")

EARLIER_WAKES = (
    "Réveils antérieurs : 8 dernières lectures au maximum sur 56 jours ; repères "
    "historiques, jamais preuve de l'état actuel. Comparer les mesures comparables, sans "
    "exiger de l'athlète une certitude sur sa base ni convertir une note qualitative en "
    "chiffre.")

EXPOSURES = "Expositions"

CONFIRMED_EXPOSURES = "Expositions confirmées (intention déclarée, jamais déduite d'une mesure) :"

RECENT_STRENGTH = "Renfo récent, déjà enregistré par la page :"

RECENT_DAILY = "État quotidien récent :"

CAPABILITIES = "Capacités"

READ_EMPTY = "{label} : aucune donnée enregistrée dans cette lecture."

EXPOSURE_LINE = (
    "- {day} | {exposure_type} | origine={origin} | tolérance={tolerance} | dose={dose} "
    "| observation={observation_key}")

STRENGTH_LINE = "renfo {workout}"

NO_DAILY_TODAY = "Aucun état déclaré pour aujourd'hui : les lignes suivantes sont historiques."

OPEN_THREADS = "Fils ouverts :"

ACTIVE_DEADLINES = "Échéances actives :"

CAPABILITIES_TITLE = "Capacités :"

READ_FAILED = "{label} : lecture échouée, état inconnu ; ce n'est pas une absence de faits."

DOSE_MINUTES = "{duration_min} min"

DOSE_TARGET = "cible {target_value} {target_unit}"

STRENGTH = "Renfo"

STRENGTH_MINUTES = "{duration_minutes} min"

DAILY_LINE = "- {day} | sommeil={sleep} | charge={life_load} | envie={desire}"

THREAD_LINE = "- {thread_key} | {body} | relance={follow_up}"

STRENGTH_PAIN = "douleur "

STRENGTH_HARD = "difficultés hautes "

STRENGTH_NOTE = "note="

DAILY = "État quotidien"

DAILY_NOTE = " | note={note}"

THREADS = "Fils"

DEADLINES = "Échéances"

DEADLINE_DETAIL = " | {item} | propriétaire={fact_owner} | bloque={blocks}"

DEADLINE_DUE = " ÉCHUE"
