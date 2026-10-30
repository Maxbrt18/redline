"""Compact, deterministic relational memory, limited to what coaching needs."""

from __future__ import annotations

import hashlib
import re

from redline.coach.fr import relationship as wording
from redline.model import voice

_SPACE = re.compile(r"\s+")

#: `source_key` prefixes of lines the service sends without a full turn: waiting
#: line, outage, limit. They stay in the database and quotable, but do not count
#: among the latest exchanges rendered to the coach.
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
    lines = [wording.HEADER]
    for row in messages[-8:]:
        coach = row["role"] != "athlete"
        speaker = wording.COACH if coach else wording.ATHLETE
        body = _SPACE.sub(" ", row["body"]).strip()
        if coach:
            body = voice.muted(body)
        if len(body) > 1200:
            body = body[:800] + wording.ABRIDGED + body[-350:]
        lines.append(wording.MESSAGE.format(
            speaker=speaker, created_at=row.get("created_at", wording.UNKNOWN_DATE),
            id=row.get("id", "?"), body=body))
    if open_questions:
        lines.append(wording.REVIEW_HOW)
        lines.append(wording.OPEN_QUESTIONS)
        for row in open_questions[-20:]:
            lines.append(wording.OPEN_QUESTION.format(
                id=row.get("id", "?"), status=row.get("status", "open"),
                asked_at=row.get("asked_at", "?"),
                follow_up_on=row.get("follow_up_on") or wording.NOT_SET,
                question=row["question"]))
            if row.get("review_note"):
                lines.append(wording.REVIEW_NOTE + row["review_note"])
        lines.append(wording.OPEN_QUESTIONS_RULE)
        if len(open_questions) > 20:
            lines.append(wording.MORE_QUESTIONS)
    if threads:
        lines.append(wording.THREADS)
        for row in threads[:15]:
            lines.append(wording.THREAD.format(
                thread_key=row["thread_key"], status=row["status"],
                follow_up_on=row.get("follow_up_on") or wording.NOT_SET, body=row["body"]))
    if resolved_questions:
        lines.append(wording.RESOLVED)
        for row in resolved_questions[:12]:
            lines.append(wording.RESOLVED_QUESTION.format(
                status=row["status"], resolved_at=row.get("resolved_at", "?"),
                question=row["question"])
                + (wording.RESOLVED_NOTE.format(review_note=row["review_note"])
                   if row.get("review_note") else ""))
        lines.append(wording.RESOLVED_RULE)
    return "\n".join(lines)
