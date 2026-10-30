"""The strength page as Messenger shows it, and the report the coach reads once it is sent."""

from __future__ import annotations

BUTTON_TITLE = "Ouvrir la séance"   # Messenger caps a button title at 20 characters

DEFAULT_TEXT = "Ta séance de renfo"

HEAD = "Renfo {type}"

DURATION = ", {minutes} min"

DONE = "Séance de renfo terminée ({what}){duration}."

REMARK_SUFFIX = " — remarque : « {note} »"

GATED = "- {name} : retiré par le garde-fou de douleur{remark}"

SKIPPED = "- {name} : pas fait{remark}"

PARTIAL = "partiel, {slots} série(s)/côté(s) validé(s)"

SCORE = "{score}/10"

RIGHT = "droite"

LEFT = "gauche"

SETS = "séries "

REMARK = "remarque : « {note} »"

EXERCISE = "- {name} : {bits}"

EXERCISE_DONE = "fait"

DIFFICULTY = ("Difficulté ressentie par exercice, 0 = facile, 10 = trop dur "
              "(0-5 on progresse, 6-7 on tient, 8-10 on régresse) :")

STABLE = "stable"

UP = "monté"

DOWN = "descendu"

PAIN_BOTH = "{label} : {before}/10 avant, {after}/10 après ({move})."

PAIN_AFTER = "{label} : {after}/10 après la séance (rien avant)."

PAIN_BEFORE = "{label} : {before}/10 avant de commencer."

ATHLETE_NOTE = "Ce que ⟨athlète⟩ ajoute : « {note} »"
