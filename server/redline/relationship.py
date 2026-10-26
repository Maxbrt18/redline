"""Mémoire relationnelle compacte, déterministe et limitée au coaching utile."""

from __future__ import annotations

import hashlib
import re

from scripts.model import voice

_SPACE = re.compile(r"\s+")

#: Préfixes de `source_key` des lignes que le service envoie sans tour de fond :
#: phrase d'attente, panne, limite. Elles restent en base et citables, mais ne
#: comptent pas parmi les derniers échanges rendus au coach.
SERVICE_LINE_PREFIXES = ("holding", "failure", "outage")


def question_fingerprint(text: str) -> str:
    normalized = _SPACE.sub(" ", text.lower()).strip()
    normalized = re.sub(r"\s*\?", "?", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()


def render_memory(messages: list[dict], open_questions: list[dict],
                  threads: list[dict] | None = None,
                  resolved_questions: list[dict] | None = None) -> str:
    if not messages and not open_questions and not threads and not resolved_questions:
        return ""
    lines = ["[SOUVENIR RELATIONNEL DURABLE — invisible pour Sam]"]
    for row in messages[-8:]:
        speaker = "Sam" if row["role"] == "athlete" else "Coach"
        body = _SPACE.sub(" ", row["body"]).strip()
        if speaker == "Coach":
            body = voice.muted(body)
        if len(body) > 1200:
            body = body[:800] + " [extrait abrégé] " + body[-350:]
        lines.append(f"- {speaker} [{row.get('created_at', 'date inconnue')}; "
                     f"message_id={row.get('id', '?')}]: {body}")
    if open_questions:
        lines.append(
            "Pour examiner une réponse, utilise /usr/local/bin/redline-agent-cli fact-write.py "
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
        lines.append("Questions restées ouvertes :")
        for row in open_questions[-20:]:
            lines.append(f"- question_id={row.get('id', '?')} "
                         f"[{row.get('status', 'open')}; posée {row.get('asked_at', '?')}; "
                         f"reprise {row.get('follow_up_on') or 'non fixée'}] : {row['question']}")
            if row.get("review_note"):
                lines.append("  Déjà compris / encore manquant : " + row["review_note"])
        lines.append("Une question ouverte est un contexte, jamais un agenda imposé. "
                     "Ne répète pas ce qui est déjà répondu. Un changement de sujet la laisse "
                     "ouverte ; cumule les réponses partielles au lieu de repartir de la dernière phrase. "
                     "Pour deferred sans date, attends que Sam reprenne le sujet ; avec une "
                     "date, aucune relance avant celle-ci. Une date échue n'impose pas de relance.")
        if len(open_questions) > 20:
            lines.append("D'autres questions sont conservées : les lire avec "
                         "/usr/local/bin/redline-agent-cli fact-read.py questions --limit 1000.")
    if threads:
        lines.append("Sujets conservés (leur présence ne demande aucune relance) :")
        for row in threads[:15]:
            lines.append(f"- {row['thread_key']} [{row['status']}; "
                         f"reprise {row.get('follow_up_on') or 'non fixée'}] : {row['body']}")
    if resolved_questions:
        lines.append("Questions déjà traitées (historique daté, pas état du jour) :")
        for row in resolved_questions[:12]:
            lines.append(f"- [{row['status']}; clôturée {row.get('resolved_at', '?')}] "
                         f"{row['question']}"
                         + (f" Réponse ou limite : {row['review_note']}"
                            if row.get('review_note') else ""))
        lines.append("Ne repose pas ces questions sans élément nouveau précis. "
                     "Une réponse ancienne n'établit pas l'état actuel.")
    return "\n".join(lines)
