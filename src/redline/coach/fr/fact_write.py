"""What `redline fact-write` answers the coach, and the statements it writes."""

from __future__ import annotations

SEALED_CHOICE = (
    "SCELLÉ, ne pas révéler à l'athlète avant le bilan {week}. Choix du coach pour "
    "{session_key} du {date:%d/%m} : {minutes} min. Prédiction : la durée courue par "
    "l'athlète tombe à ±10 min de ce choix.")

STAGED = "{entity_key} préparé pour le tour."

ALREADY_RECORDED = "{entity_key} déjà enregistré, rien d'écrit."

RECORDED = "{entity_key} enregistré."
