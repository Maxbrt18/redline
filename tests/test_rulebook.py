"""The rulebook: the instance's own, or the engine default; short, without history,
and what the coach reads is what the code applies."""

import os
import re
import tomllib
from pathlib import Path

import pytest
from conftest import VAULT_ROOT

from redline.model import rulebook, transitions, typography

DEFAULT_TEXT = rulebook.DEFAULT.read_text(encoding="utf-8")


def _variant(tmp_path: Path, text: str) -> Path:
    (tmp_path / rulebook.FILENAME).write_text(text, encoding="utf-8")
    return tmp_path


def test_without_its_own_rulebook_a_vault_gets_the_engine_default(tmp_path):
    assert rulebook.load(tmp_path).source == rulebook.DEFAULT


def test_an_instance_rulebook_replaces_the_default_entirely(tmp_path):
    vault = _variant(tmp_path, DEFAULT_TEXT.replace("transitions_per_week = 1",
                                                    "transitions_per_week = 2"))
    book = rulebook.load(vault)
    assert book.source == vault / rulebook.FILENAME
    assert book.parameters.transitions_per_week == 2
    assert "2 par semaine au plus" in next(r.text for r in book.rules if r.id == "n8")


def test_the_current_rulebook_follows_vault_path(tmp_path, monkeypatch):
    vault = _variant(tmp_path, DEFAULT_TEXT.replace('slug = "terrain"', 'slug = "piste"'))
    monkeypatch.setenv("VAULT_PATH", str(vault))
    assert "piste" in transitions.kinds() and "terrain" not in transitions.kinds()
    assert transitions.label("piste") == "terrain neuf"


def test_an_edited_rulebook_is_read_again_without_restart(tmp_path):
    vault = _variant(tmp_path, DEFAULT_TEXT)
    assert rulebook.load(vault).parameters.stall_weeks == 3
    path = vault / rulebook.FILENAME
    path.write_text(DEFAULT_TEXT.replace("stall_weeks = 3", "stall_weeks = 4"), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert rulebook.load(vault).parameters.stall_weeks == 4


def test_a_cited_threshold_is_the_applied_value_said_in_french():
    book = rulebook.parse(DEFAULT_TEXT, rulebook.DEFAULT)
    gate = next(rule for rule in book.rules if rule.id == "po")
    assert "1,5/10" in gate.text and "{" not in book.render()


def test_the_wake_trace_cutoff_is_a_local_time():
    book = rulebook.parse(DEFAULT_TEXT, rulebook.DEFAULT)
    assert book.parameters.wake_trace_until.isoformat() == "14:00:00"
    assert book.parameters.spoken()["wake_trace_until"] == "14:00"


@pytest.mark.parametrize("change, message", [
    (("pain_cap = 2", "pain_cap = 2\ncap_douleur = 3"), "inconnu"),
    (("plafond {pain_cap}/10", "plafond {douleur_max}/10"), "seuil inconnu douleur_max"),
    (('id = "n8"', 'id = "n9"'), "absente du règlement : n8"),
    (('slug = "terrain"', 'slug = "descent"'), "transition en double : descent"),
    (("assimilation_weeks = []", 'assimilation_weeks = ["W35"]'), "semaine ISO"),
    (('slug = "terrain"', 'slug = "Terrain neuf"'), "slug"),
    (('wake_trace_until = "14:00"', 'wake_trace_until = "14h"'), "HH:MM"),
])
def test_a_malformed_rulebook_is_refused_with_its_reason(tmp_path, change, message):
    old, new = change
    assert old in DEFAULT_TEXT
    with pytest.raises(rulebook.RulebookError, match=message):
        rulebook.load(_variant(tmp_path, DEFAULT_TEXT.replace(old, new, 1)))


def test_the_default_rulebook_declares_every_rule_the_engine_enforces():
    ids = {entry["id"] for entry in tomllib.loads(DEFAULT_TEXT)["rules"]}
    assert set(rulebook.ENFORCED) <= ids


def test_the_rulebook_is_short_enough_to_travel_with_every_packet():
    # It is injected on every N1+ turn: a rulebook that grows becomes a palimpsest
    # again. Raising this cap requires removing a rule, not adding one.
    assert len(rulebook.render().split()) <= 400


def test_the_rulebook_uses_only_phone_friendly_signs():
    assert [glyph for glyph in typography.BANNED_GLYPHS if glyph in rulebook.render()] == []


def test_the_default_carries_no_history():
    """The rule in force, never its history: no date in the engine default."""
    assert not re.search(r"20\d\d-\d\d-\d\d|\bW\d\d\b", rulebook.render())


def test_no_fixed_percentage_bounds_the_long_run():
    """The progression of the long run is judged, no fixed percentage bounds it.
        The only percentages left are those of assimilation (n2) and of the pain
        reduction (n3)."""
    for rule in rulebook.current().rules:
        if rule.id in {"n2", "n3"}:
            continue
        assert not re.search(r"\d+\s?%", rule.text), rule.id
    assert "sans pourcentage fixe" in rulebook.render()


def test_every_rule_names_an_owner_that_exists():
    for rule in rulebook.current().rules:
        for owner in rule.owner.split(" ; "):
            path = owner.split(" §")[0].split(" 20")[0].strip()
            assert (VAULT_ROOT / path).exists(), f"{rule.id} : propriétaire absent {path}"
