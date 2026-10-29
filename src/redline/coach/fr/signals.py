"""The weak signals, as the coach reads them."""

from __future__ import annotations

FLAG = "  ⚑ {label} — {detail}"

HR_AT_PACE = (
    "{date:%d/%m} à {minutes}:{seconds:02d}/km : {hr:.0f} bpm contre {reference:.0f} "
    "(médiane de {n} EF voisines, {delta:+.0f})")

CADENCE_UP = "cadence en hausse à allure facile"

CADENCE_UP_DETAIL = (
    "{before:.0f} → {now:.0f} pas/min ({label}). Cause inconnue : vérifier d'abord si la "
    "cadence était prescrite ou volontaire, puis le terrain, l'allure, la facilité du "
    "geste et la douleur")

HR_HIGH = "FC haute à allure comparable, deux EF de suite"

HR_HIGH_DETAIL = (
    ". Vérifier d'abord sommeil, maladie, chaleur non mesurée et terrain : un signal "
    "n'est pas une cause")

TITLE = "  Signaux faibles — {first} → {last}"

CADENCE_ROW = "  Cadence (EF/SL, pas/min)   "

LONGEST_ROW = "  Plus longue (km)           "

HR_AT_PACE_ROW = "  FC à allure (EF plates)    "

NO_COMPARABLE = "aucune EF comparable"

NOTHING = "  Rien à signaler sur ces semaines."

STRAVA_CHECKS = "  Les 4 contrôles Strava du § D (allure en descente, parcours écourtés,"

STRAVA_CHECKS_END = "  pauses en fin de sortie, longueur des notes) restent à lire chez Strava."

EMPTY_LEDGER = "Registre vide : rien à lire."
