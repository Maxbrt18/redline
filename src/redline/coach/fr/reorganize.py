"""A reorganised week as the coach reads it: the variants, what each bends, what is left."""

from __future__ import annotations

HEADER = "Variantes de {week} (révision {revision}), calculées sans IA :"
VARIANT = "Variante {number} — {summary}"
MOVED_ONE = "1 date change"
MOVED_MANY = "{count} dates changent"
DROPPED_ONE = "1 séance saute"
DROPPED_MANY = "{count} séances sautent"
UNCHANGED = "rien ne bouge"
MOVE = "  - {title} : {before} → {after}"
DROP = "  - {title} : saute"
CARRIED = "  - le groupe du {day} porte {title}"
AROUND = " (~{minutes} min à courir autour)"
OTHERS = "{count} autres placements respectent les filtres ; ils changent plus ou plient plus."
HELD_BACK = ("Signal de douleur actif : {titles} ne peut pas être avancé par une "
             "réorganisation. Le reculer reste possible ; l'avancer est un jugement de coach.")
NONE = ("Aucune variante ne respecte les filtres avec ces jours. La demande relève du coach : "
        "changer une séance, en retirer une clé ou lever un filtre, avec une raison écrite.")

#: What a bent preference says, next to the variant that bends it.
REMARKS = {
    "impact_near_hard": "    · {title} (avec sauts) tombe la veille ou le jour d'une séance exigeante.",
    "load_above_plan": "    · 3 jours plus chargés que le pic du plan (+{value} %).",
    "long_off_days": "    · {title} quitte ses jours habituels ({day}).",
    "group_uncarried": "    · Aucune séance ne va avec le groupe du {day}.",
    "rest_day_used": "    · {title} tombe sur un jour de repos prévu ({day}).",
}
