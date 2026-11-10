"""The progression proposals, as `redline progression` and the Sunday review show them."""

from __future__ import annotations

HEADER = (
    "Propositions de progression du renfo (moteur en lecture seule : il propose, il "
    "n'impose rien et ne bloque aucune écriture ; l'athlète peut toujours dire non). "
    "Cite la proposition et sa clause quand tu fixes une dose ; la dose reste celle de la "
    "fiche tant que tu ne l'as pas changée :")

NONE_DECLARED = (
    "Aucune lignée de renfo déclarée dans [progression] de rulebook.toml : pas de "
    "proposition.")

UNAVAILABLE = "Propositions de progression indisponibles : {reason}"

#: The move, as the coach reads it.
MOVES = {"up": "monter", "hold": "tenir", "down": "redescendre"}

LINE = "- {lineage} ({family}{group}) : {move} · clause {clause}{overruled}"

OVERRULED = " (la règle disait {move})"

GROUP = ", groupe {group}"

DOSE = "  dose : {anchor} → {dose}"

NO_DOSE = "  dose : aucune dose lisible sur la fiche"

CEILING = "  plafond : {ceiling}"

NO_CEILING = "  plafond : aucun (la taille du pas revient au prescripteur)"

VERDICTS = "  verdicts : {verdicts}"

NO_VERDICT = "  verdicts : aucune exposition lue"

VERDICT = "{criterion} {verdict}{comparison}{fact}"

COMPARISON = " ({value:g} pour {threshold:g})"

FACT = " · {fact}"

#: The verdict of a criterion.
VERDICT_WORDS = {"pass": "tenu", "fail": "manqué", "unknown": "inconnu"}

MISSING = "  lectures manquantes : {signals}"

UNREAD = "  séances sans lecture requise, écartées : {days}"

VETOES = "  vetos : {vetoes}"

VETO = "{kind} ({rule} : {fact})"

#: The veto kinds, as the coach reads them.
VETO_KINDS = {"gate": "porte", "quality_hold": "qualité retirée", "taper": "affûtage",
              "assimilation": "assimilation", "budget": "budget de hausses"}

REENTRY = "  retour après une absence : la dose redescend avant de remonter"

STALE = ("  lecture trop ancienne : la dernière séance lue date du {day:%d/%m}, les "
         "suivantes n'ont pas leur lecture ; la dose tient jusqu'à la prochaine séance lue")

LADDER_END = "  bout de l'échelle : une hausse demande une variante ou une nouvelle plage"

#: The readings the engine can ask for, as the coach asks for them.
SIGNALS = {
    "session_rpe": "RPE de séance",
    "reserve_reps": "répétitions en réserve",
    "set_reps_min": "compte de répétitions par série",
    "difficulty": "difficulté de l'exercice (0-10)",
    "effort_pain": "douleur d'effort",
    "following_pain": "douleur des sorties suivantes",
    "wake_above_base": "trace du lendemain matin",
    "gut_symptoms": "symptômes digestifs",
    "carbs_taken": "glucides pris",
    "week_effort_peak": "pic de douleur d'effort de la semaine",
    "week_peak_rise": "hausse du pic de douleur",
    "week_wakes_above_base": "réveils au-dessus de la base",
    "week_wake_rising": "tendance des réveils",
    "week_persistent_wakes": "réveils persistants",
    "week_easy_hr_drifts": "dérives de FC en EF",
    "week_sleep_debt": "dette de sommeil",
}
