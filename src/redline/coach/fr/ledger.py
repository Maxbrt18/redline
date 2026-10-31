"""The ledger as the coach reads it: the week report, pain, gate and trajectory."""

from __future__ import annotations

REST_BASELINE = "repos 8 sem : {series}"

OVER_TARGET = "{date} {kind} — RPE {rpe:g} déclaré pour une cible de {target:g}"

PAIN_SERIES = "pic {peak} · médiane {median} (n={n})"

NO_REST_HISTORY = "aucun historique de repos sur 8 sem"

GATE_WAKES = "réveils : {measured} chiffré(s) sur {wakes}"

GATE_RED = "rouge"

VOLUME_RUN = "{km_run:.2f} km courus ({outings})"

TRAJECTORY_WEEK = "{label}  {km:6.1f} km   plus longue {longest:5.1f}"

STALL = (
    "{head}Plus longue sans nouveau record depuis {weeks} semaine(s) close(s) — record "
    "{record_km:.1f} km en {record_week}")

PROJECTION = (
    "Course du {race_on:%d/%m} : {weeks} semaine(s) utile(s) avant l'affûtage "
    "({taper_from}), depuis {start_km:.1f} km (plus longue des 30 j).")

REPORT_WEEK = "Semaine {label}"

REPORT_VOLUME = "Volume : {week_volume}"

REPORT_VOLUME_SOURCE = (
    "(le registre, pas le plan — `Vol. cible` de current-plan.md est une intention, pas "
    "un fait)")

REPORT_GAUGE = "Jauge : {h_eq:.2f} h-éq ({level}/100{over_level}) · ratio {ratio}{over_ratio}"

REPORT_INSTRUMENTS = "(sommeil={sleep}, charge_vie={life_load})"

TRACE_SESSION = "{date} {type} → {weighted:.2f} h-éq"

TRACE_TWO_HARD_DAYS = "×1.15 deux jours exigeants consécutifs"

TRACE_BAD_SLEEP_WEEK = "×1.20 sommeil dégradé toute la semaine"

TRACE_HIGH_LIFE_LOAD = "×1.10 grosse charge de vie"

PAIN_WEEK = "{week} : effort {effort} · repos {rest}"

GATE_WAKES_REFERENCE = ", référence 8 sem {base:g}"

GATE_INCOMPLETE = "incomplète"

GATE_GREEN = "verte"

GATE_VERDICT = "Porte (règle po, calculée) : {verdict}"

VOLUME_WALK = " + {km_walk:.2f} km marchés ({walks})"

TRAJECTORY_OPEN_WEEK = "   ⧗ en cours, {elapsed} j sur 7 — hors verdict"

SLOPE_UNAVAILABLE = "pente non calculable — {closed} semaine(s) close(s) sur les {needed} qu'il faut"

SLOPE_VOLUME = "Volume"

SLOPE_LONGEST = "Plus longue"

SLOPE_LINE = "{name} : {now:.1f} contre {then:.1f} sur les {trajectory_half} d'avant — {change}"

STALL_RECORD_BEATEN = (
    "Plus longue : record de {record_km:.1f} km battu en {record_week}, la dernière "
    "semaine close")

STALL_RULE_S9 = (
    " · règle s9 : le protocole d'instruction des baisses s'ouvre maintenant, sans "
    "attendre son jalon daté")

PLANNED_GAP = "{session} {planned:.1f} km = {gap:+.0f} % de {longest:.1f} km (plus longue des 30 j)"

PROJECTION_NO_TARGET = (
    "Cible de sortie longue non fournie (--long-run-target KM) : rythme nécessaire non "
    "calculé.")

PROJECTION_EVERY_WEEK = "toutes les semaines"

PROJECTION_WEEKS_LOST = "{lost} semaine(s) sans hausse"

PROJECTION_REQUIRED_NOTE = (
    ". Un rythme, pas une règle : il se juge sur la tolérance observée et l'historique.")

REPORT_DESCENT = "D− de la semaine : {dminus} m — c'est LUI que compte la jauge"

REPORT_OVER_TARGET = "⚠ au-dessus de la cible — {overshoot}"

REPORT_INCOMPLETE = "⚠ ligne incomplète — {problem}"

REPORT_TRAJECTORY = "Trajectoire — {weeks} dernières semaines"

TREND_TITLE = "Tendance douleur — le PIC fait foi (le déclaratif est biaisé bas)."

TREND_SERIES = (
    "Deux séries : douleur PENDANT l'effort, douleur AU REPOS. Le n compte : un max sur 7 lectures\n"
    "n'est pas comparable à un max sur 3.\n")

EMPTY_LEDGER = "Registre vide."

TRACE_MISSED = "{date} {type} séance RATÉE (+0.5)"

TRACE_BAD_NIGHTS = "×1.10 2-3 mauvaises nuits"

PAIN_WEEK_WAKE = " · réveil {wake}"

GATE_NO_EFFORT_PEAK = "pic d'effort : aucune course chiffrée"

GATE_EFFORT_PEAK = "pic d'effort {now:g}/10 (au plus {ceiling:g})"

GATE_TREND_PARTIAL = "tendance : {previous} {before} → {label} {now}"

GATE_TREND = "tendance : {before:g} → {now:g}"

GATE_HR_NORMAL = "FC des footings normale"

GATE_SLEEP_UNDECLARED = "sommeil non déclaré"

GATE_SLEEP = "sommeil {sleep}"

PLANNED_NO_RECENT_RUN = "{session} {planned:.1f} km : aucune course dans les 30 j, écart non calculable"

PROJECTION_REQUIRED = "Pour {target_km:.1f} km avant l'affûtage, il faut en moyenne "

REPORT_PAIN = "Douleur {label} : {trend}"

REPORT_ESCULIER = "⚠ Esculier {zone_prefix}{flag}"

REPORT_LONGEST = (
    "Plus longue des 30 j : {longest:.1f} km — repère, pas un plafond (aucun pourcentage "
    "fixe)")

TREND_NO_ZONE = "Aucune zone surveillée ([[injuries]] de redline.toml) : rien à suivre."

TREND_ESCULIER = (
    "\n"
    "  Règle Esculier (≤2/10, pas pire le lendemain) :")

ESCULIER_OVER_CAP = "AU-DESSUS DU PLAFOND"

ESCULIER_OVER_CAP_DETAIL = "{type} à {pain}/10 > {esculier_cap} → réduction immédiate"

GATE_HR_HIGH = "FC des footings haute : "

PROJECTION_IMPOSSIBLE = "impossible"

PROJECTION_GROWTH = "+{percent:.0f} %/sem"

REPORT_NO_RACE = (
    "Projection course : aucune course typée (redline fact-write deadline --kind race) — "
    "rien à projeter.")

TREND_NOTHING = "    rien à signaler"

ESCULIER_UNCHECKED = "non vérifié"

ESCULIER_UNCHECKED_DETAIL = "{type} à {pain}/10, aucune lecture « reveil » le lendemain"

ESCULIER_WAKE_TRACE = "TRACE AU RÉVEIL"

ESCULIER_WAKE_TRACE_DETAIL = "{after}/10 au réveil après {type} à {pain}/10 — {rest_baseline}"

GATE_WAKES_ABOVE = " · au-dessus : "

SLEEP_UNDECLARED = "non déclaré"

LIFE_LOAD_UNDECLARED = "non déclarée"


def outings(count: int) -> str:
    """`1 sortie`, `3 sorties`."""
    return f"{count} sortie{"s" if count > 1 else ""}"

PAIN_READING = "douleur {zone}={value}"

MISSING_CELLS = "{date} {type} : {join} non renseigné(s)"

ROUNDED = "« {name} » : {raw} → {good} (arrondi)"

STAGED = "préparée pour le tour"

RECORDED = "enregistrée"

OBSERVATION_WRITTEN = "{date} {type} : observation {oid} {state}."

HR_DOUBT_STAGED = "{key} : FC douteuse ({hr_doubt}) préparée pour le tour."

HR_DOUBT_RECORDED = "{key} : FC moyenne déclarée douteuse ({hr_doubt})."

MEASUREMENT_STAGED = "{key} : {metric} préparée pour le tour."

MEASUREMENT_RECORDED = "{key} : {what} complétée."

LINK_STAGED = "{key} -> Strava {strava} : rattachement préparé pour le tour."

LINK_RECORDED = "{key} -> Strava {strava} : observation rattachée."

STAGED_MASCULINE = "préparé pour le tour"

RECORDED_MASCULINE = "enregistré"

START_WRITTEN = "Départ facultatif {state}."

MATCH_WRITTEN = "Appariement {state}."

TAPER = "Affûtage : référence {reference_km:.1f} km/sem ({span}) · {weeks} · baisse moyenne des semaines closes {percent:.0f} %{band}"

TAPER_BAND = " (visée {low}-{high} %)"

TAPER_WEEK = "{week} {km:.1f} km"

TAPER_WEEK_OPEN = "{week} {km:.1f} km (en cours)"

TAPER_OPENING = ("Affûtage : référence {reference_km:.1f} km/sem ({span}) · {weeks} · "
                 "la baisse se mesure à la clôture de la semaine")

TAPER_NO_REFERENCE = "Affûtage : aucune course à pied en {span}, baisse non mesurable."

TAPER_TOO_CUT = "⚠ Affûtage coupé au-delà de {high} % : on arrive désentraîné plutôt que reposé."

TAPER_LOOSE = "⚠ Affûtage relâché : baisse sous {low} %, la fatigue n'a pas eu le temps de partir."
