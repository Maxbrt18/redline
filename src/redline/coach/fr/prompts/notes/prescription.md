[CONSIGNE TYPÉE, invisible pour ⟨athlète⟩. Ce qui se juge après la sortie s'écrit dans
la séance, jamais dans la seule `note`. Relief permis : `dplus_cap_m`, `dminus_cap_m`,
cible facultative `dplus_target_m`. Montées : `climb_intent` = `easy` (marche permise,
défaut), `run_slow` (toutes courues lentement, la FC peut monter), `race_effort`,
`hard` (réservé aux côtes, dont c'est le défaut) ou `power_hike` avec
`power_hike_from_pct`. Descentes : `descent_intent` = `relaxed` (défaut), `held` ou
`worked`. Une EF ou une SL à efforts prescrits (pyramide, lignes, structure d'un
groupe) porte `structure` = `efforts`, sans `hr_zone` ni `hr_cap_bpm` : les efforts
montent la FC par construction. Une séance de répétitions type sa dose : `reps` et
`rep_seconds`, `recovery_seconds`, et hors côtes `rep_pace_s` (s/km) ; au-dessus du
plafond de sa lignée (`redline progression`), un seul cran, sans veto, avec
`override_reason` et `override_prediction` (une prédiction ouverte), jamais deux fois de
suite. Un cycle est toujours en cours (`redline cycle show`) : la porte tient la semaine
à son volume, sa SL, son nombre de qualités et ses séances clés (une semaine d'affûtage à
la semaine d'avant l'affûtage, sa coupe à la bande). Une séance qui réalise une séance
clé porte `key_position` (le numéro entre crochets) et garde sa famille, sa dose et sa
bande d'allure. Un cran au-delà se déclare dans `cycle_overrides` (`target` volume,
long_run, key_dose ou rep_pace, `session` pour les deux derniers, `reason`, `prediction`
ouverte), jamais deux semaines de suite ; volume et SL seulement en préparation
générale. Au-delà, change la version : `redline cycle level --set <niveau> --decision ...`
pour la préparation générale, `redline cycle propose` pour une préparation, que l'athlète
prend lui-même. Jamais de
`rpe_min`/`rpe_max` : le RPE se calcule sur la dose, après la séance. Une EF se borne par
sa durée ou sa distance, jamais les deux, et ne porte aucune allure : elle se cale sur
la FC et l'effort.]
