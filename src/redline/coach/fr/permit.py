"""The easy-run permit as the coach reads it, at the review or on request."""

from __future__ import annotations

TRACE_NEXT_DAY = "trace au-dessus de la base le lendemain"
OVER_ENVELOPE = "au-delà de l'enveloppe ({cap} min)"
MINOR_ENVELOPE = "un peu au-delà de l'enveloppe ({cap} min)"
#: The HR is the body's answer, not the athlete's choice: never called "too fast".
HR_ABOVE_CAP = "FC moyenne au-dessus du plafond ({hr_avg} > {hr_cap})"
MINOR_HR = "FC moyenne un peu au-dessus du plafond ({hr_avg} > {hr_cap})"
SECOND_MINOR = "deuxième écart mineur de la période"
MINORS = " (mineur : {minors})"
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

TITLE = "Permis EF : {phase}."
WITHDRAWAL = "Retrait actif jusqu'au {until:%d/%m}."
GAP = ", écart {gap:+.0f} min ({agreement})"
AGREEMENT = "accord"
DISAGREEMENT = "désaccord"
TRACE_UNKNOWN = "trace J+1 inconnue"
TRACE = "trace J+1 {trace}"
IN_GROUP = " · en groupe"
DETAIL_RUN = "{chosen}, couru {actual}{agreement} · {trace}{group}"
CHOSEN = "coach {coach} min"
ENVELOPE_ONLY = "enveloppe {cap}"
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
