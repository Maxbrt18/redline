"""The coach's texts live in `src/redline/coach/fr/`: every file is used, every value filled."""

from __future__ import annotations

import re

from conftest import ENGINE_ROOT

from redline.coach import fr
from redline.service import prompts

PROMPTS = ENGINE_ROOT / "src" / "redline" / "coach" / "fr" / "prompts"
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _names() -> set[str]:
    return {path.relative_to(PROMPTS).with_suffix("").as_posix()
            for path in PROMPTS.rglob("*.md")}


def test_every_prompt_file_is_loaded_by_the_service():
    source = (ENGINE_ROOT / "src" / "redline" / "service" / "prompts.py").read_text(encoding="utf-8")
    triggers = {"triggers/" + kind.replace("_", "-") for kind in prompts.PROACTIVE_TRIGGERS}
    depth = {f"notes/depth-{level.lower()}" for level in prompts.DEPTH_NOTES}
    named = {name for name in _names()
             if f'"{name.removeprefix("notes/")}"' in source}
    assert _names() == named | triggers | depth


def test_a_note_is_one_paragraph():
    for path in (PROMPTS / "notes").glob("*.md"):
        lines = path.read_text(encoding="utf-8").removesuffix("\n").split("\n")
        assert all(line and line == line.strip() for line in lines), path.name


def test_no_injected_text_keeps_a_placeholder():
    gate = [{"question": "Q ?", "on_fail": "repli", "holds": ["s1"]}]
    texts = [
        prompts.STYLE_PROMPT, prompts.TRIAGE_PROMPT, prompts.TRIAGE_STRUCTURED_PROMPT,
        prompts.strava_snapshot_note({"id": 1}), prompts.gate_note(gate, gate),
        prompts.gate_note([], gate, closing=True),
        *prompts.DEPTH_NOTES.values(),
        *(prompts.build_proactive_prompt(kind, "42") for kind in prompts.PROACTIVE_TRIGGERS),
    ]
    for text in texts:
        assert not PLACEHOLDER.search(text), PLACEHOLDER.search(text)


def test_fill_replaces_only_the_values_given():
    assert fr.fill("{a} et {b}", a="1") == "1 et {b}"


def test_the_texts_name_the_athlete_by_the_token_only():
    """The test athlete's name, written out, would reach a real athlete as is."""
    for path in (ENGINE_ROOT / "src" / "redline" / "coach").rglob("*"):
        if path.suffix in {".md", ".py"}:
            assert not re.search(r"\bsam\b", path.read_text(encoding="utf-8"),
                                 re.IGNORECASE), path.name
