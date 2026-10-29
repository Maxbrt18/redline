"""The backward feasibility of the race firsts, as the review reads it."""

from __future__ import annotations

LONG_RUN_STEP = "palier de SL"

NOTHING_NEW = "rien de neuf"

NO_FIRST = "aucune"

SESSION_FIRST = "{label} du {day:%d/%m} ({session_id})"

CURRENT_RULES = "règles actuelles"

OPTION_STEPS = "{prefix}{label} : {long_run_steps} palier(s) de SL possibles : {line}"

RENDER_HEADER = (
    "Faisabilité à rebours (règle n8, calculée) : course le {race_on:%d/%m}, affûtage "
    "{taper_start}-{taper_end}.")

RENDER_ASSUMPTIONS = (
    "Hypothèses : {transitions_per_week} transition(s) par semaine (n8), rien en "
    "assimilation (n2) ni en affûtage ; un palier de SL n'est pas une transition, il "
    "passe par la porte. Placement possible, pas prescription.")

INFEASIBLE = "INFAISABLE avec les règles actuelles. Un seul levier à la fois :"

RENDER_NO_DECISION = (
    "Le solveur ne tranche rien : le levier se choisit au bilan, avec l'athlète, et se "
    "logge dans decisions.md.")

LEVER_EXTRA_TRANSITION = "une transition de plus par semaine (règle n8)"

OPTION_NOT_ENOUGH = "{prefix}{label} : ne suffit pas"

RENDER_NO_WEEK = "Aucune semaine ne peut plus porter de nouveauté avant l'affûtage."

LEVER_WITHOUT = "sans {label} avant la course"

LEVER_ASSIMILATION = "une transition en semaine d'assimilation (règle n2)"

RENDER_FIXED = "Déjà au programme : "

FEASIBLE = "FAISABLE, "

RENDER_FIRSTS = ". Premières fois à placer : "

RENDER_DONE = " ; déjà faites : "

RENDER_WEEKS = "Semaines utiles {first}-{last}"

RENDER_ASSIMILATION = ", dont {weeks} en assimilation"

NO_RACE = (
    "Faisabilité à rebours : aucune course typée à venir (`redline fact-write deadline "
    "--kind race`).")
