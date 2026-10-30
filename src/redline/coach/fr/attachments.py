"""What the service says about an attachment: to the athlete when it is refused, to the coach in
the conversation trace."""

from __future__ import annotations

# The conversation trace: no URL, file name or raw content.
LABEL = "[Pièce jointe : {kinds}]"

PHOTO = "photo"

DOCUMENT = "document"

UNSUPPORTED_MEDIA = "média non pris en charge"

UNKNOWN_FORMAT = "format inconnu"

# Why an attachment is refused.
UNREADABLE = "Cette pièce jointe est illisible. Renvoie-la une fois."

UNKNOWN_VERSION = "Cette pièce jointe utilise un format que je ne reconnais pas."

COUNT = "Envoie entre 1 et {max} photos ou documents à la fois."

INCOMPLETE = "Cette pièce jointe est incomplète. Renvoie-la une fois."

UNKNOWN_KIND = "Ce type de pièce jointe n'est pas pris en charge."

NO_VIDEO = ("Je ne traite pas les vidéos ni l'audio : l'analyse serait trop peu "
            "précise. Envoie plutôt une photo nette ou un document.")

WHAT_TO_SEND = "Envoie une photo, un PDF ou un fichier texte."

UNSAFE_URL = "L'adresse de cette pièce jointe n'est pas une URL Meta sûre."

TOTAL_TOO_LARGE = "L'ensemble des pièces jointes dépasse la limite autorisée."

DOWNLOAD_FAILED = "Le téléchargement Meta a échoué. Renvoie le fichier dans un instant."

UNSAFE_REDIRECT = "La redirection de la pièce jointe n'est pas sûre."

DOWNLOAD_REFUSED = "Meta ne me laisse plus télécharger ce fichier. Renvoie-le."

TOO_LARGE = "Ce fichier dépasse la limite de {megabytes} Mo."

IMAGE_FORMATS = "JPEG, PNG ou WebP"

DOCUMENT_FORMATS = "PDF, TXT ou CSV"

FORMAT = "Format non pris en charge. Formats acceptés : {accepted}."

EMPTY = "Le fichier reçu est vide."

MISMATCH = "Le contenu réel du fichier ne correspond pas au format annoncé."
