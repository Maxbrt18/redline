"""What the service itself says to the athlete when a turn cannot answer.

Each sentence says what happens for the athlete and what comes next, never the
mechanism, and is sent once.
"""

from __future__ import annotations

from redline.coach.dates import listing
from redline.model import commands

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


ATTACHMENT_NOTHING_RELIABLE = (
    "Je n'arrive pas à extraire quelque chose de fiable de cette pièce jointe.")

#: What `/renfo` answers when the card cannot go out, by state.
STRENGTH_CARD_LIMITS = {
    "unavailable": (
        "Je n'ai pas de programme fiable pour aujourd'hui en ce moment, donc je ne "
        "peux pas te confirmer s'il y a du renfo. Je m'en occupe."),
    "stale": (
        "Ta semaine a bougé depuis le dernier programme : je le remets à jour avant "
        "de t'envoyer la carte de renfo."),
    "blocked": (
        "Le renfo du jour est prévu, mais j'attends d'abord ta réponse à ma question "
        "du jour avant de t'envoyer sa carte."),
    "none": "Aucune séance de renforcement n'est prescrite aujourd'hui.",
}

STRENGTH_CARD_FAILED = (
    "La séance de renfo est bien prévue, mais je n'arrive pas à générer sa carte pour "
    "l'instant.")

STRENGTH_TITLE = "renforcement"

STRENGTH_CARD = "Ta carte de renfo du jour — {title}."

NOTEBOOK_UNAVAILABLE = "Le carnet est momentanément indisponible."

NOTEBOOK_REVOKED = ("Les accès au carnet sont révoqués. Tape /carnet pour reconnecter ton "
                    "appareil.")

NOTEBOOK_INVITE = "Ton carnet : ta séance du jour, ta semaine et ta course."

NOTEBOOK_BUTTON = "Ouvrir mon carnet"

CLEARED = "Nouvelle conversation. Je repars du contexte frais du vault au prochain message."

CANCEL_REQUESTED = ("Annulation demandée. Le travail en cours va s'arrêter sans être "
                    "relancé automatiquement.")

NOTHING_TO_CANCEL = "Aucun travail en cours à arrêter."

DEEP_WITHOUT_QUESTION = "Ajoute la question dans le même message : /deep <ta question>"

# `/status`.
STATUS = "Moteur : {provider}\n{running}\n{session}\nRoutage :\n{routing}"

STATUS_RUNNING = "Travail en cours : {model}/{effort}, depuis {elapsed}, watchdog dans {watchdog}"

STATUS_DEFAULT_MODEL = "modèle par défaut"

STATUS_DEFAULT_EFFORT = "effort par défaut"

STATUS_CANCELLING = ", annulation en cours"

STATUS_IDLE = "Aucun travail en cours"

STATUS_SESSION = "Session active, dernier échange il y a {age}, contexte frais dans {fresh_in}"

STATUS_NO_SESSION = "Aucune session active, le prochain message relira le vault"

STATUS_HEAVY_ROUTE = "proactif lourd"

STATUS_ROUTE = "{label} : {route}"

STATUS_HOURS = "{hours} h {minutes:02d}"

STATUS_MINUTES = "{minutes} min"

NOTEBOOK_ARCHIVE = "Tes cartes et bilans restent dans ton carnet."

SESSION_BUTTON_TEXT = "Ta séance"
