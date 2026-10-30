"""Descriptive measures and an experimental variant, never imported by the coach."""
from __future__ import annotations

import hashlib
import re
from collections import Counter

from redline.coach.fr import evaluation as wording
from redline.service.prompts import EMOTION_NOTE, STYLE_PROMPT
from redline.service.textutils import burst_parts, strip_links

EXPERIMENT = wording.EXPERIMENT


def instructions(variant: str, constitution: str) -> str:
    if variant not in {"current", "contextual"}:
        raise ValueError("unknown variant")
    experiment = EXPERIMENT if variant == "contextual" else ""
    # Explicit order: the business rules stay whole. The experimental adjustment only
    # touches the way of answering. No call to the database or Messenger.
    return "\n\n".join((constitution, STYLE_PROMPT, experiment, wording.CONVERSATION_FICTIONAL))


def emotion_note(variant: str, emotional: bool) -> str:
    if not emotional:
        return ""
    if variant == "contextual":
        return wording.CONTEXTUAL_EMOTION
    return EMOTION_NOTE


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def measure(answer: str) -> dict:
    """Observable signals, without claiming to grade naturalness or safety."""
    delivered = strip_links(answer).strip()
    words = re.findall(r"\b[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)*\b", delivered)
    # The transport's 1900-character split stays out of this counter: these are the
    # logical bubbles before the Meta limit, not the guaranteed number of HTTP sends.
    return {
        "words": len(words), "chars": len(delivered),
        "questions": delivered.count("?"),
        "bubbles": len(burst_parts(delivered)) if delivered else 0,
        "list_lines": len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s", delivered)),
        "opening": " ".join(w.lower() for w in words[:5]),
    }


def shape_checks(answer: str, turn: dict) -> dict:
    metrics = measure(answer)
    return {
        "nonempty": bool(answer.strip()),
        "question_budget": metrics["questions"] <= turn["max_questions"],
        "message_budget": metrics["bubbles"] <= turn["max_messages"],
    }


def compare_runs(left: dict, right: dict) -> dict:
    """Strict pairing: an error or a missing case never becomes a win."""
    def indexed(run):
        rows = run["results"]
        index = {(r["id"], r["repeat"], r["turn"]): r for r in rows}
        if not rows or len(index) != len(rows):
            raise ValueError("empty results or duplicate identities")
        if any("error" in r or not r.get("answer", "").strip() for r in rows):
            raise ValueError("incomplete comparison: a generation failed or an answer is empty")
        return index

    a, b = indexed(left), indexed(right)
    if left["fixtures_sha256"] != right["fixtures_sha256"] or a.keys() != b.keys():
        raise ValueError("scenarios, repeats and turns must be identical")
    pairs, blind, key = [], [], []
    for number, identity in enumerate(sorted(a), 1):
        x, y = a[identity], b[identity]
        if (x["model"], x["effort"]) != (y["model"], y["effort"]):
            raise ValueError("comparing a prompt needs the same model and the same effort")
        mx, my = measure(x["answer"]), measure(y["answer"])
        pairs.append({"id": identity[0], "repeat": identity[1], "turn": identity[2],
                      "left": mx, "right": my,
                      "delta_words": my["words"] - mx["words"],
                      "left_shape_ok": all(x["checks"].values()),
                      "right_shape_ok": all(y["checks"].values())})
        # A reproducible pseudo-random order, only roughly balanced.
        swap = int(digest(repr(identity))[:8], 16) % 2 == 1
        ordered = (y, x) if swap else (x, y)
        blind.append({"pair": number, "scenario": x["scenario"], "turn": identity[2],
                      "A": {"history": ordered[0]["history"], "answer": ordered[0]["answer"]},
                      "B": {"history": ordered[1]["history"], "answer": ordered[1]["answer"]},
                      "winner": "", "reason": "", "safety_regression": None})
        key.append({"pair": number, "A": right["variant"] if swap else left["variant"],
                    "B": left["variant"] if swap else right["variant"]})
    return {
        "notice": "The deltas describe the shape. No automatic semantic winner.",
        "pairs": pairs, "blind_review": blind, "key": key,
        "repeated_openings": {
            side: {text: count for text, count in Counter(
                measure(r['answer'])["opening"] for r in run["results"]).items() if count > 1}
            for side, run in (("left", left), ("right", right))},
    }
