"""What the service itself says to the athlete when a turn cannot answer.

Each sentence says what happens for the athlete and what comes next, never the
mechanism, and is sent once.
"""

from __future__ import annotations

from scripts.model import commands

from .calendar import listing

TIMED_OUT = ("Je n'ai pas réussi à finir ma réponse à temps. Redis-moi l'essentiel en une "
             "phrase et je repars de là.")
INTERRUPTED = ("Ma réponse s'est interrompue en route. Renvoie-moi ton message quand tu veux, "
               "je reprends de zéro.")
OUTAGE_NOTICE = (
    "Je n'arrive pas à te répondre tout de suite. Je garde ton message et je te réponds "
    "dès que possible.")
EMPTY_REPLY = "Je n'ai pas réussi à formuler ma réponse. Redis-moi l'essentiel et je repars de là."
EMPTY_REPLY_APPLIED = (
    "J'ai bien noté ce que tu m'as donné, mais ma réponse ne s'est pas écrite. Dis-moi si "
    "tu attendais autre chose.")
PLAN_UNAVAILABLE_LINE = (
    "Je n'ai pas de programme valide pour cette semaine en ce moment, donc pas de séance "
    "à te donner tant qu'il n'est pas refait. Je m'en occupe.")
ATTACHMENT_UNREADABLE = (
    "Je n'ai pas réussi à lire ce fichier en entier. Envoie-moi seulement la page ou "
    "l'image qui compte.")
UNRECORDED_GENERIC = (
    "Attention : ce que tu viens de me donner n'a pas pu être enregistré. Ne le considère "
    "pas comme noté ; redis-le-moi dans un moment.")

#: What the athlete gave, in their words. Internal commands (a declared question, an
#: angle, a thread) are absent: losing them costs the athlete nothing.
UNRECORDED_LABELS = {
    commands.OBSERVATION_ADD: "ta séance",
    commands.EXPOSURE_UPSERT: "ta séance",
    commands.MEASUREMENT_COMPLETE: "tes ressentis sur la séance",
    commands.HR_DOUBT: "ta FC douteuse",
    commands.PLAN_REVISE: "le changement de programme",
    commands.DAILY_UPSERT: "ton état du jour",
    commands.SESSION_START: "ton départ",
    commands.SESSION_MATCH: "le rattachement de ta sortie",
    commands.ACTIVITY_LINK: "le rattachement de ta sortie",
    commands.GEAR_UPSERT: "ton matériel",
}


def unrecorded(labels: list[str]) -> str:
    return (f"Attention : je n'ai pas pu enregistrer {listing(labels)}. Ne le considère pas "
            "comme noté ; redis-le-moi dans un moment.")
