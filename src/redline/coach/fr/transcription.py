"""What the service says around a voice note: to the speech-to-text model before it
listens, to the coach with the transcript, to the athlete when it cannot be heard."""

from __future__ import annotations

# Read by the speech-to-text model as the text that precedes the audio.
CONTEXT = "Message vocal d'un athlète à son coach de course à pied."

# Coaching jargon a generic model misspells. Session kinds come from
# `session_labels`, the instance's own words from `redline.toml`.
TERMS = ("VMA", "RPE", "FC", "D+", "dénivelé", "fractionné", "fartlek", "allure",
         "PPG", "gainage", "récup", "échauffement", "Strava")

# How a transcript reaches the coach: the words were heard, not typed.
MESSAGE = ("[Message vocal, transcrit automatiquement : un mot ou un chiffre peut avoir été "
           "mal entendu. Un chiffre décisif qui paraît incohérent se vérifie avec "
           "l'athlète.]\n{text}")

# How the conversation history keeps it.
TRACE = "[Vocal] {text}"

UNREADABLE = "Je n'ai pas réussi à transcrire ce vocal. Renvoie-le, ou écris-le-moi."

SILENT = "Je n'ai rien entendu dans ce vocal. Renvoie-le, ou écris-le-moi."
