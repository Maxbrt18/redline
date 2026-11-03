"""A reorganised week as the coach reads it: the variants, what each bends, what is left."""

from __future__ import annotations

HEADER = "Variantes de {week} (révision {revision}), calculées sans IA :"
VARIANT = "Variante {number} — {summary}"
MOVED_ONE = "1 date change"
MOVED_MANY = "{count} dates changent"
DROPPED_ONE = "1 séance saute"
DROPPED_MANY = "{count} séances sautent"
UNCHANGED = "rien ne bouge"
MOVE = "  - {title} : {before} → {after}"
DROP = "  - {title} : saute"
CARRIED = "  - le groupe du {day} porte {title}"
AROUND = " (~{minutes} min à courir autour)"
OTHERS = "{count} autres placements respectent les filtres ; ils changent plus ou plient plus."
HELD_BACK = ("Signal de douleur actif : {titles} ne peut pas être avancé par une "
             "réorganisation. Le reculer reste possible ; l'avancer est un jugement de coach.")
NONE = ("Aucune variante ne respecte les filtres avec ces jours. La demande relève du coach : "
        "changer une séance, en retirer une clé ou lever un filtre, avec une raison écrite.")

#: What a bent preference says, next to the variant that bends it.
REMARKS = {
    "impact_near_hard": "    · {title} (avec sauts) tombe la veille ou le jour d'une séance exigeante.",
    "load_above_plan": "    · 3 jours plus chargés que le pic du plan (+{value} %).",
    "long_off_days": "    · {title} quitte ses jours habituels ({day}).",
    "group_uncarried": "    · Aucune séance ne va avec le groupe du {day}.",
    "rest_day_used": "    · {title} tombe sur un jour de repos prévu ({day}).",
}

DAYS_HEADER = "Jours déclarés pour {week} (habitudes et jours dits d'avance), à placer autour :"
DAYS_NONE = "Aucun jour déclaré pour {week} : ni habitude, ni jour dit d'avance."
DAYS_LINE = "  - {day} : {state}{habit}"
HABIT = " (habitude)"

# ------------------------------------------------------------------ the request to the coach

#: The message the coach receives when the athlete asks from the notebook. It names the
#: command that answers it: a request with no field to land in ends up in prose.
ASK = (
    "[Demande du carnet] Réorganiser {week} au-delà des dates. Jours : {days}. "
    "Souhait : {wish}.{context} {found} Réponds par une à trois propositions avec "
    "`redline week-plan propose --week {week} --input <fichier>` "
    "(`{{\"proposals\": [{{\"title\", \"body\", \"revision\"}}]}}`, chaque revision complète "
    "comme pour revise) ; l'athlète en prend une d'un tap dans le carnet. Si rien ne doit "
    "changer, dis-le simplement, sans proposition.")
WISHES = {"intensity": "plus d'intensité", "volume": "plus de volume",
          "other": "autre chose, voir le contexte"}
CONTEXT = " Contexte, en ses mots : « {context} »."
FOUND = "Le calcul sans IA propose {count} variante(s) de dates ; la meilleure : {best}."
FOUND_NONE = "Le calcul sans IA ne trouve aucune variante de dates qui respecte les filtres."
FOUND_UNWRITTEN = "La semaine n'est pas encore écrite."
PROPOSED = "Propositions enregistrées pour {week}, sur la révision {based_on} :"
PROPOSED_STAGED = "Propositions préparées pour {week}, sur la révision {based_on} (enregistrées à la fin du tour) :"
PROPOSAL = "  {number}. {title}"

# ------------------------------------------------------------------ the revision it writes

REASON = "Semaine réorganisée dans le carnet, variante {number} sur {count} : {summary}."
#: The fact behind every change: what the athlete said of their days.
EVIDENCE = "availability: carnet {stamp}, {days}"
NO_DAY = "aucun jour déclaré"
CHANGE_CONTEXT = "Variante {number} sur {count} calculées sans IA, choisie dans le carnet."
CHANGE_RULE = "réorganisation : seules les dates changent, filtres du moteur respectés"
DROP_CONTEXT = "Les jours manquent : la séance de plus faible priorité saute."
REJECTED = "non choisie dans le carnet"
ALTERNATIVE = "Variante {number} : {summary}"
DAY = "{day} {state}"
PINNED = "{title} épinglée {day}"
MOVED = "{title} passe du {before} au {after}"
DROPPED = "{title} saute"
ADDED = "{title} s'ajoute le {day}"
CHANGED = "{title} change, le {day}"
#: How a day the athlete stated reads in the evidence.
DAY_STATES = {
    "as_planned": "comme prévu",
    "off": "pas de course",
    "short": "court seulement ({minutes} min)",
    "easy": "facile seulement",
    "more": "peut en faire plus",
    "with_others": "avec d'autres (~{minutes} min, {pace}, {firmness})",
    "other_sport": "{sport} ({effort}, {run})",
}
PACES = {"easy": "tranquille", "mixed": "variable", "workout": "séance"}
FIRMNESS = {True: "obligatoire", False: "si ça rentre"}
EFFORT = {True: "exigeant", False: "léger"}
RUN_TOO = {True: "course possible", False: "sans course"}
