"""The dossier the service computes before the Sunday review."""

from __future__ import annotations

HEADER = (
    "[DOSSIER DU BILAN, calculé par le service avant ce tour, invisible pour l'athlète. "
    "Ces chiffres sont déjà calculés par les mêmes fonctions que les CLI : cite-les, ne "
    "les recalcule pas ; relance une CLI seulement pour vérifier un point précis.]")

SIGNALS = "Signaux faibles (4 semaines) :"

NOTHING = "rien à signaler"

PREDICTIONS_DUE = (
    "Prédictions échues ({typed}), ferme chacune avec `redline fact-write prediction "
    "--key <clé> --outcome true|false|void`. Un verdict s'adosse à un fait lu ; void est "
    "un verdict, l'invention n'en est pas un. Une réfutée porte `--reason optimistic` "
    "(moins bien que prédit) ou `cautious` (mieux) ; une void `--reason instrument` (la "
    "mesure n'existait pas), `exposure` (la situation n'a pas eu lieu), `scope` (le "
    "critère exigeait un inventaire exhaustif) ou `superseded` (retirée avant sa revue) :")

PREDICTION_LINE = "- {prediction_key} (échue le {as_date:%d/%m}) : {statement}"

CALIBRATION = "Calibration des prédictions typées par domaine (cite-la dans le bilan) :"

NO_PREDICTION = "aucune prédiction typée"

DEADLINES_DUE = (
    "Échéances échues ({overdue}). Pour chacune qui dépend d'une action de l'athlète, "
    "donne-lui l'action prête à faire et une règle de repli datée :")

DEADLINE_LINE = "- {deadline_key} (due le {as_date:%d/%m}) : {item}"

DEADLINE_BLOCKS = " ; bloque {blocks}"

PERMIT = (
    "Permis EF de la semaine close (choix scellés du coach face au couru) : solde chaque "
    "prédiction easy-permit-* avec ces écarts, puis scelle les EF de la semaine suivante "
    "avec `redline fact-write permit` AVANT d'écrire la révision. L'EF ne porte que son "
    "enveloppe (`duration_cap_min`), jamais ta durée, et ton choix ne se révèle pas avant"
    " le bilan (protocols.md § Permis EF) :")

CAPABILITIES = "Capacités (dernière évaluation) : "

FOOTER = "[/DOSSIER]"
