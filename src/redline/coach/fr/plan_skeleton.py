"""What the coach reads of a plan skeleton: candidates, chosen skeleton, weekly bounds."""


def number(value: float) -> str:
    """`46,2`: one decimal at most, with a decimal comma."""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


HEADER = ("Squelette à rebours — {race} le {race_on:%d/%m/%Y} ({distance} km, {terrain}{goal}), "
          "gabarit {template}")
GOAL = ", objectif {time} ({source})"
GOAL_SOURCES = {"athlete": "choix de l'athlète", "coach": "posé avec le coach",
                "equivalence": "équivalence d'une course récente"}
START = ("Point de départ : {volume} km/semaine en moyenne sur les 3 semaines closes, plus longue "
         "sortie des 30 jours {long_run} km{recovery}.")
RECOVERY = " ; course récente : récupération d'abord"
HELD = ("Ce que l'athlète a tenu (contre-lecture) : une qualité ajoute {premium} de charge à ses "
        "km, plus forte semaine {load}, sortie longue jusqu'à {share} des km de sa semaine. "
        "La charge est celle du RPE attendu : km effort × intensité au carré, une EF compte "
        "ses km.")
UNKNOWN = "inconnu"
LOAD = ", charge {load:g}"
NO_LOAD = ", charge inconnue"
ESTIMATE = ("Équivalence VDOT : {time} (VDOT {vdot}, depuis {km} km en {source} le "
            "{on:%d/%m/%Y}). Une estimation, pas un objectif : si l'athlète la retient, "
            "`goal_source = equivalence`.")
NO_ESTIMATE = ("Équivalence VDOT : aucune course à plat d'au moins {km} km dans les {weeks} "
               "dernières semaines.")
CANDIDATE = ("Candidat « {candidate} » : bloc spécifique {specific} sem., décharge toutes les "
             "{deload} sem., 2e qualité à partir du {second}.")
CHOSEN = "Squelette choisi : « {candidate} » (spécifique {specific} sem., décharge /{deload}, 2e qualité en {second})."
NONE_CHOSEN = ("Aucun squelette choisi : choisis-en un avec l'athlète, puis "
               "`redline plan-skeleton --choose <candidat>`.")
NO_RISK = "  risques : aucun"
RISKS = "  risques : {risks}"
RISK_TEXT = {
    "base_short": "base de {value:g} sem. pour {reference:g} au minimum",
    "peak_volume_unreached": "pic de volume à {value:g} km au lieu de {reference:g}",
    "peak_long_run_unreached": "plus longue sortie à {value:g} km au lieu de {reference:g}",
    "specific_short": "bloc spécifique de {value:g} sem. pour {reference:g} demandées",
    "load_above_held": "{week} : charge {value:g}, au-delà de la plus forte semaine tenue ({reference:g})",
    "long_share_above_held": ("{week} : sortie longue à {value:g} % des km de la semaine, "
                              "au-delà de ce qui a été tenu ({reference:g} %)"),
}
PHASES = {"recovery": "récup", "base": "base", "development": "développement",
          "specific": "spécifique", "taper": "affûtage"}
SECOND = {"development": "développement", "specific": "spécifique"}
WEEK = "  {week} {phase}{deload} : {volume:g} km{load}, SL {long_run}, {qualities}{intent}"
DELOAD = " (décharge)"
RACE_WEEK = "course"
LONG_RUN = "{km:g} km au plus"
NO_QUALITY = "aucune qualité"
QUALITIES = "{count} qualité{plural} au plus ({families})"
INTENTS = {"easy": "", "run_slow": ", montées en courant lentement",
           "power_hike": ", marche active dès {grade} %", "race_effort": "", "hard": ""}
CHOSEN_STAGED = "Squelette « {candidate} » choisi pour la course du {race_on:%d/%m} : {entity_key}."
UNKNOWN_CANDIDATE = "candidat inconnu : {candidate} (disponibles : {known})"
BOUNDS = ("Squelette « {candidate} », semaine {week} ({phase}{deload}) : volume {volume:g} km au "
          "plus, SL {long_run}, {qualities}.")
