"""What the notebook says to the athlete."""

from __future__ import annotations

#: The answers to the question asked after a high heart rate at a comparable pace.
HR_REASONS = {"sommeil": "nuit courte", "rhume": "début de rhume",
              "chaleur": "chaleur ou terrain", "rien": "rien de spécial"}

#: Why no session is shown, by plan status name.
PLAN_MESSAGES = {
    "INVALID": "Le programme de la semaine n’est pas encore prêt : il arrive "
               "dans Messenger.",
    "STALE": "Une activité non prévue doit être arbitrée : le coach revoit "
             "le programme avant la prochaine séance.",
    "AWAITING": "Une question t’attend dans Messenger avant une séance.",
}

#: Shown beside the pain points so a figure can be read back.
PAIN_SCALE = [
    {"value": 1, "text": "La tension est là, pas gênante."},
    {"value": 2, "text": "Tu commences à douter, tu changerais ta foulée ; le lendemain, conséquences légères."},
    {"value": 3, "text": "« J’en ai fait trop » : 1 à 2 semaines de charge à revoir."},
]
