"""Reusable rendering of a validated strength selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from redline.cards import html, render
from redline.model import paths, strength_log, vault_layout
from redline.model.program import load


def exercise_row(index: int, prescription) -> str:
    rest = prescription.dose.rest_s
    return (
        '<li class="row">'
        f'<span class="row__index">{index:02d}</span>'
        '<div class="row__main">'
        f'<p class="row__name">{html.esc(prescription.name)}</p>'
        + (f'<p class="row__sub">{html.esc(prescription.target)}</p>'
           if prescription.target else "")
        + "</div>"
        '<div class="row__dose">'
        f'<p class="row__value">{html.esc(prescription.dose.human())}</p>'
        + (f'<p class="row__sub">repos {rest} s</p>' if rest else "")
        + "</div></li>"
    )


def page_session(built: dict, history: dict) -> dict:
    def exercise(prescription) -> dict:
        data = {"slug": prescription.slug, "name": prescription.name,
                "target": prescription.target, "dose": prescription.dose.payload()}
        data.update(cue=prescription.cue, equipment=list(prescription.equipment),
                    load=prescription.load)
        last = strength_log.human(history.get(prescription.slug) or {})
        if last:
            data["last"] = last
        return data

    session = {
        "sid": built["sid"], "session": built["session"],
        "title": built["title"], "rule": built["rule"], "date": built["date"],
        "duration_min": built["duration_min"], "note": built["note"],
        "exercises": [exercise(item) for item in built["exercises"]],
    }
    if built.get("gate"):
        session["gate"] = built["gate"]
    return session


def snapshot(built: dict, *, root: Path | None = None) -> dict:
    root = root or paths.vault_root()
    history = strength_log.last_by_slug(root / vault_layout.STRENGTH_LOG_CSV)
    return page_session(built, history)


def subtitle(built: dict) -> str:
    return ", ".join(value for value in [built["title"], built["date"]] if value)


def default_session_id(out: Path, built: dict) -> str:
    identity = page_session(built, {})
    identity.pop("sid", None)
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:10]
    return f"{out.stem[:53]}-{digest}"


def render_card(selection: dict, out: Path, *, root: Path | None = None,
                html_only: bool = False) -> tuple[Path, dict]:
    """Validates, resolves and renders the card; errors surface to the owner."""
    root = root or paths.vault_root()
    selection = dict(selection)
    explicit_sid = "sid" in selection
    selection.setdefault("sid", out.stem)
    built = load(root).build(selection)
    if not explicit_sid:
        built["sid"] = default_session_id(out, built)

    card_subtitle = ", ".join(
        value for value in [built["date"], f"~{built['duration_min']} min"] if value)
    rows = "".join(exercise_row(index + 1, item)
                   for index, item in enumerate(built["exercises"]))
    body = (
        '<main id="card">' + html.header(built["title"], card_subtitle)
        + f'<ol class="rows">{rows}</ol>'
        + html.footer(built["note"], built["rule"]) + "</main>")
    target = out.with_suffix(".html") if html_only else out
    if html_only:
        target.write_text(render.document(body), encoding="utf-8")
    else:
        render.render(body, str(target))
    return target, built
