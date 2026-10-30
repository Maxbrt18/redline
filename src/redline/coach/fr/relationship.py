"""The relational memory the service puts before the coach: latest exchanges, questions, threads."""

from __future__ import annotations

HEADER = "[SOUVENIR RELATIONNEL DURABLE — invisible pour ⟨athlète⟩]"

ATHLETE = "⟨athlète⟩"

COACH = "Coach"

ABRIDGED = " [extrait abrégé] "

UNKNOWN_DATE = "date inconnue"

MESSAGE = "- {speaker} [{created_at}; message_id={id}]: {body}"

REVIEW_HOW = (
    "Pour examiner une réponse, utilise /usr/local/bin/redline-agent-cli fact-write "
    "question --question-id <UUID> --answer-id <UUID> --status <état> --note "
    "'Ce qui est compris et ce qui manque'. Les UUID sont ceux de ce souvenir. "
    "answered exige une réponse suffisante à toute la question ; open conserve "
    "une réponse partielle ; deferred respecte une reprise ultérieure "
    "(--follow-up-on YYYY-MM-DD seulement si convenu) ; expired ferme un sujet "
    "refusé ou impossible à préciser, sans valider le fait manquant. Un oui, un chiffre, "
    "un mot commun ou une reply Messenger ne prouvent "
    "pas automatiquement que toute la question est réglée : interprète-les dans "
    "l'ensemble de l'échange, avec les faits déjà fournis. Une estimation "
    "prudente (je dirais oui, probablement pareil) est une réponse, pas une "
    "absence : conserve sa réserve dans la note, sans exiger de certitude. "
    "answered signifie information suffisante, pas certitude absolue. "
    "Une information impossible à préciser ne se redemande pas : ferme la "
    "collecte avec sa limite explicite, sans lever un contrôle de sécurité "
    "qui en dépend encore.")

OPEN_QUESTIONS = "Questions restées ouvertes :"

NOT_SET = "non fixée"

OPEN_QUESTION = "- question_id={id} [{status}; posée {asked_at}; reprise {follow_up_on}] : {question}"

REVIEW_NOTE = "  Déjà compris / encore manquant : "

OPEN_QUESTIONS_RULE = (
    "Une question ouverte est un contexte, jamais un agenda imposé. "
    "Ne répète pas ce qui est déjà répondu. Un changement de sujet la laisse "
    "ouverte ; cumule les réponses partielles au lieu de repartir de la dernière phrase. "
    "Pour deferred sans date, attends que ⟨athlète⟩ reprenne le sujet ; avec une "
    "date, aucune relance avant celle-ci. Une date échue n'impose pas de relance.")

MORE_QUESTIONS = ("D'autres questions sont conservées : les lire avec "
                  "/usr/local/bin/redline-agent-cli fact-read questions --limit 1000.")

THREADS = "Sujets conservés (leur présence ne demande aucune relance) :"

THREAD = "- {thread_key} [{status}; reprise {follow_up_on}] : {body}"

RESOLVED = "Questions déjà traitées (historique daté, pas état du jour) :"

RESOLVED_QUESTION = "- [{status}; clôturée {resolved_at}] {question}"

RESOLVED_NOTE = " Réponse ou limite : {review_note}"

RESOLVED_RULE = ("Ne repose pas ces questions sans élément nouveau précis. "
                 "Une réponse ancienne n'établit pas l'état actuel.")

EXPIRED_UNREVIEWED = "Fermée sans examen après {days} jours."

REPLY_HEADER = "[RÉPONSE MESSENGER CIBLÉE — invisible]\n"

REPLY_TO_COACH = "du coach"

REPLY_TO_ATHLETE = "de ⟨athlète⟩"

REPLY_QUOTED = "Le message courant répond directement à ce message antérieur {owner} :\n« {body} »"

REPLY_LOST = (
    "Le message courant est une réponse directe à un ancien message "
    "Messenger, mais son contenu n'est plus disponible localement. "
    "N'invente pas la citation.")

REPLY_RULE = ("\nInterprète ce lien comme une réponse ciblée, pas comme un nouveau sujet. "
              "Ne récite pas cette note.")

CORRECTIONS = "[CORRECTIONS PERTINENTES — invisible, à ne pas réciter]\n- "
