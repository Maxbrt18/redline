"""The weather as the coach names it: WBGT flags and the sky of a WMO code."""

from __future__ import annotations

FLAGS = {"white": "blanc", "green": "vert", "yellow": "jaune", "red": "rouge", "black": "noir"}

# The WMO codes Open-Meteo returns, the subset that matters for running.
SKY = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grésil",
    80: "averses faibles", 81: "averses", 82: "averses violentes",
    85: "averses de neige", 86: "averses de neige fortes",
    95: "orage", 96: "orage avec grêle", 99: "orage avec forte grêle",
}
UNKNOWN_SKY = "conditions inconnues"


def sky(weather: dict) -> str:
    """The sky of a stored weather reading, from its `weather_code`."""
    code = weather.get("weather_code")
    return SKY.get(code, UNKNOWN_SKY) if isinstance(code, int) else UNKNOWN_SKY
