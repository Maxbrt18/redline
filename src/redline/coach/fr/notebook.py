"""What the notebook says to the athlete."""

from __future__ import annotations

from redline.model.notebook_views import ScaleStep

#: The answers to the question asked after a high heart rate at a comparable pace.
HR_REASONS = {"sleep": "nuit courte", "cold": "début de rhume",
              "heat": "chaleur ou terrain", "none": "rien de spécial"}

#: Why no session is shown, by plan status name.
PLAN_MESSAGES = {
    "INVALID": "Le programme de la semaine n’est pas encore prêt : il arrive "
               "dans Messenger.",
    "STALE": "Une activité non prévue doit être arbitrée : le coach revoit "
             "le programme avant la prochaine séance.",
    "AWAITING": "Une question t’attend dans Messenger avant une séance.",
}

#: Shown beside the pain points so a figure can be read back.
PAIN_SCALE: list[ScaleStep] = [
    {"value": 1, "text": "La tension est là, pas gênante."},
    {"value": 2, "text": "Tu commences à douter, tu changerais ta foulée ; le lendemain, conséquences légères."},
    {"value": 3, "text": "« J’en ai fait trop » : 1 à 2 semaines de charge à revoir."},
]

CARBS = "glucides {grams} g/h"

MOVED = "Déplacée le {day:%d/%m}."

CHANGED = "Modifiée le {day:%d/%m}."

RACE_START = "Départ"

#: Written in the daily note with the athlete's answer; read back to know the question is closed.
HR_MARKER = "FC haute du {day:%d/%m} :"

WAKE_TRACE_NOTE = "trace au réveil déclarée dans le carnet"

# What the notebook tells the coach after an answer.
ALERT_HR = ("Carnet : FC haute à allure comparable le {day:%d/%m} ({hr} contre {reference}), "
            "sans explication de sommeil, de maladie ni de chaleur.")

ALERT_WAKE_TRACE = ("Carnet : trace nette au réveil ce matin{where} ({today:%d/%m}). La "
                    "condition écrite dans la séance du jour s'applique.")

ALERT_PAIN = ("Carnet : douleur {value:g}/10 au pic{where} sur la séance {kind} du {day:%d/%m}, "
              "au-dessus du plafond de {cap:g}.")

ZONE = " ({label})"

# Why an answer is refused.
NUMBER_EXPECTED = "Valeur numérique attendue."

OUT_OF_SCALE = "Valeur attendue entre 0 et 10."

HALF_POINTS = "Douleur au demi-point près."

INTEGER_EXPECTED = "Valeur entière attendue."

QUESTION_CLOSED = "Cette question est déjà close. Actualise le carnet."

UNEXPECTED_ANSWER = "Réponse inattendue. Actualise le carnet."

TRACE_EXPECTED = "Trace attendue : aucune, très légère, légère ou nette."

ALREADY_FILLED = "Cette mesure est déjà renseignée. Actualise le carnet."

#: The errors the notebook page shows, by cause.
ERRORS = {
    "forbidden": "Accès refusé.",
    "unavailable": "Carnet temporairement indisponible. Réessaie dans un instant.",
    "signed_out": "Ouvre un nouveau lien avec /carnet dans Messenger.",
    "storage": "Stockage temporairement indisponible.",
    "bad_link": "Lien invalide.",
    "link_used": "Ce lien a expiré ou a déjà servi. Tape /carnet dans Messenger.",
    "bad_page": "Page invalide.",
    "bad_week": "Semaine invalide.",
    "bad_zone": "Zone invalide.",
    "no_zone": "Aucune zone surveillée.",
    "bad_session": "Séance invalide.",
    "no_session": "Séance introuvable.",
    "bad_request": "Demande invalide.",
    "bad_date": "Date invalide.",
    "bad_answer": "Réponse invalide.",
    "session_changed": "La séance a changé ou n’est plus disponible. Actualise le carnet.",
    "no_card": "Carte introuvable.",
}

# Why the strength session cannot start.
STRENGTH_PLAN_UNCHECKED = "Le programme doit être vérifié avant de lancer une séance."

STRENGTH_PLAN_MISSING = "Le programme n’est pas disponible pour le moment."

STRENGTH_NONE = "Aucun renfo à faire aujourd’hui."

STRENGTH_BLOCKED = "Ta séance attend une réponse dans Messenger avant de démarrer."

STRENGTH_DONE = "Ton renfo du jour est enregistré."

CARD_TITLE = "Carte"

APP_NAME = "Carnet Redline"

PAGE_TITLE = "Mon carnet — Redline"
