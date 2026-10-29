"""The easy-run permit as the coach reads it, at the review or on request."""

from __future__ import annotations

TRACE_NEXT_DAY = "trace nette le lendemain"
OVER_ENVELOPE = "au-delà de l'enveloppe ({cap} min)"
TOO_FAST = "trop rapide (FC moyenne {hr_avg} > {hr_cap})"
HIGH_LIFE_LOAD = "charge de vie élevée déclarée le {day:%d/%m}"
POOR_NIGHT = "mauvaise nuit déclarée le {day:%d/%m}"
GROUP_RUN = "sortie de groupe le {day:%d/%m}"
FAILURE = "{session} : {triggers}"

#: The status of one easy run of the permit, by slug.
STATUSES = {"upcoming": "à venir", "run": "courue", "skipped": "sautée"}
#: Where the permit stands, by slug.
PHASES = {
    "withdrawn": "retrait : conduite accompagnée",
    "supervised": "conduite accompagnée",
    "granted": "permis",
    "refused": "conduite accompagnée (obtention refusée)",
}
#: The next-morning trace, by its value on the scale.
TRACES = {0: "aucune", 0.5: "très légère", 1: "légère", 2: "nette"}

TITLE = "Permis EF : {phase}."
WITHDRAWAL = "Retrait actif jusqu'au {until:%d/%m}."
GAP = ", écart {gap:+.0f} min ({agreement})"
AGREEMENT = "accord"
DISAGREEMENT = "désaccord"
TRACE_UNKNOWN = "trace J+1 inconnue"
TRACE = "trace J+1 {trace}"
IN_GROUP = " · en groupe"
DETAIL_RUN = "coach {coach} min, couru {actual}{agreement} · {trace}{group}"
DETAIL = "coach {coach} min"
MINUTES = "{minutes} min"
LINE = "- {weekday} {date:%d/%m} {session} [{status}] : {detail}{flags}"
RESTRICTION = "Restriction {week} : {reasons}."
GRANT = ("Obtention ({week}) : {run} EF courues (≥{needed} requises), {agreements} accords à "
         "±{tolerance} min, retraits : {failures} ⇒ {verdict}.")
GRANTED = "OBTENU"
NOT_GRANTED = "NON OBTENU"
COUNTER = "Compteur d'obtention : {run} EF courues sur {needed} requises d'ici la fin de {week}."
NOT_DETECTED = "Non détectés automatiquement : parcours neuf ; grosse forme "
BIG_FORM_UNSET = "(seuil non fixé, se lit sur « FC à allure » de redline signals)."
BIG_FORM = "(seuil {threshold})."
