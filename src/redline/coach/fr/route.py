"""The race route profile as the athlete reads it (the operator route-profile command)."""

from __future__ import annotations

SOURCE = {
    "ign": "altitude IGN RGE ALTI (1 m), lissée à {sigma:g} m",
    "gpx": "altitude du GPX, non lissée : pentes moins sûres qu'avec l'IGN",
}

HEADER = "{name} — {length:.1f} km · {source}"

TOTALS_BAND = ("D+ {dplus:.0f} m (de {dplus_low:.0f} à {dplus_high:.0f} selon l'échelle) · "
               "D− {dminus:.0f} m (de {dminus_low:.0f} à {dminus_high:.0f})")

TOTALS = "D+ {dplus:.0f} m · D− {dminus:.0f} m"

HIGH_POINT = "Point haut {high:.0f} m au km {high_km:.1f}"

STRETCH = "  km {start:5.1f} → {end:5.1f} : D+ {dplus:4.0f} m · D− {dminus:4.0f} m"

PLAN_HEADER = ("{name} — {length:.1f} km, soit {flat:.1f} km à plat équivalent · allure tenue "
               "{pace} à plat équivalent · arrêts {stop:g} min par ravitaillement")

CURVE_DECLARED = "Courbe : montées Strava 2017, descentes mesurées sur l'athlète (redline.toml)"

CURVE_DEFAULT = "Courbe : Strava 2017 en montée et en descente (aucune courbe déclarée)"

PASSAGE = "  km {km:5.1f}  {name:<24} {time}  ({low}-{high})"

FINISH = ("  Arrivée {km:5.1f} km                {time}  ({low}-{high} ; 9 fois sur 10 : "
          "{low90}-{high90})")

FAST_BOUND = "  Avec les montées courues comme à l'entraînement : arrivée en {time}"

BAND = "Fourchette : erreur mesurée de la méthode, ±{median:g} % (9 fois sur 10 : ±{p90:g} %)."

NOT_MODELLED = ("Non modélisés : la nuit, la fatigue des longues durées et le terrain technique ; "
                "ils rallongent le temps.")

CLOCK = "    passage vers {clock}"

WEATHER = "{temp:.0f} °C (ressenti {feels:.0f} °C), vent {wind:.0f} km/h, pluie {precip:.1f} mm/h"

WEATHER_LATER = ("Météo : pas encore de prévision pour cette date (Open-Meteo prévoit à environ "
                 "16 jours).")

FUEL = "    glucides depuis le passage précédent : {grams:.0f} g en {duration}"

FUEL_HEADER = ("Glucides à {carbs:g} g/h de course, arrêts exclus{mix}. Pas de cible de boisson "
               "en mL/h : boire à la soif.")

FUEL_MIX = ", en mélange glucose-fructose"
