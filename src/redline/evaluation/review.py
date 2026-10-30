"""Reply judge of the evaluation bench, outside production.

It is no longer part of the Messenger reply path, because refusals and timeouts would
replace already written replies with generic sentences. It only serves
`redline.cli.evaluate_coaching`, on scenarios.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redline.coach.fr import evaluation as wording
from redline.model.errors import ExternalError

if TYPE_CHECKING:  # annotations only: the bench imposes no engine at import
    from redline.evaluation.runner import OneShot
    from redline.service.agent import RunSpec


class ReviewUnavailable(ExternalError):
    """No verdict obtained: an unavailability, distinct from a semantic refusal."""


REVIEW_REASONS = ("none", "unconfirmed_action", "plan_conflict", "unsupported_claim",
                  "unsafe_advice", "repeated_question", "unclear")


@dataclass(frozen=True)
class ReviewVerdict:
    approved: bool
    reason: str
    detail: str

    def __bool__(self) -> bool:
        return self.approved


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "reason": {"type": "string", "enum": list(REVIEW_REASONS)},
        "detail": {"type": "string", "maxLength": 500},
    },
    "required": ["approved", "reason", "detail"],
    "additionalProperties": False,
}


REVIEW_RULES = wording.REVIEW_RULES


def review_prompt(answer: str, request: str, plans: dict[str, str], *,
                  stale: tuple[str, ...] = (), awaiting: tuple[str, ...] = (),
                  today: str, context: str = "", receipts: tuple[dict, ...] = (),
                  evidence: dict | None = None) -> str:
    return REVIEW_RULES + "\n" + json.dumps({
        "today": today, "request": request, "answer": answer,
        "context": context, "receipts": receipts, "plans": plans,
        "evidence": evidence or {}, "stale": list(stale), "awaiting": list(awaiting),
    }, ensure_ascii=False)


def review_result(raw: str) -> bool | ReviewVerdict:
    """A usable refusal is strictly typed; no free text lifts the check."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ReviewUnavailable("unreadable review verdict") from exc
    # Compatible with the strict boolean replies of the previous contract.
    if isinstance(data, dict) and set(data) == {"approved"}:
        if type(data["approved"]) is bool:
            return data["approved"]
        raise ReviewUnavailable("badly typed review verdict")
    if not isinstance(data, dict) or set(data) != {"approved", "reason", "detail"}:
        raise ReviewUnavailable("unreadable review verdict")
    accepted, reason, detail = data["approved"], data["reason"], data["detail"]
    if (type(accepted) is not bool or reason not in REVIEW_REASONS
            or not isinstance(detail, str) or len(detail) > 500):
        raise ReviewUnavailable("unreadable review verdict")
    if accepted:
        if reason == "none" and not detail:
            return ReviewVerdict(True, reason, detail)
        raise ReviewUnavailable("approbation contradictoire")
    if reason == "none" or not detail.strip():
        raise ReviewUnavailable("unreadable review verdict")
    return ReviewVerdict(False, reason, detail)


def judge(runner: OneShot, prompt: str, *, spec: RunSpec) -> bool | ReviewVerdict:
    """One isolated verdict: a one-off turn, without shell, style or session memory."""
    try:
        raw = runner.one_shot(prompt, spec=spec, output_schema=REVIEW_SCHEMA,
                              label="__coaching_eval_review__")
    except Exception as exc:
        raise ReviewUnavailable(type(exc).__name__) from exc
    return review_result(raw)
