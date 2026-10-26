"""Les notes de capacité ne dupliquent plus l'état opérationnel PostgreSQL."""

import pytest
from conftest import VAULT_ROOT

from scripts.model.capabilities import CAPABILITY_SCHEMA, load
from scripts.model.notes import NoteError

VALID = {
    "type": "capacite",
    "capacite_num": 1,
    "aliases": ["durabilité"],
}

FRONTMATTER = """---
type: capacite
aliases: [durabilité]
capacite_num: 1
---

Le pourquoi, avec ses [[liens]].
"""


def capability(tmp_path, text=FRONTMATTER, name="cap1-durabilite.md"):
    folder = tmp_path / "coaching" / "capabilities"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_real_note_loads_with_its_identity(tmp_path):
    loaded = load(capability(tmp_path))["cap1-durabilite"]
    assert loaded["capacite_num"] == 1
    assert loaded["aliases"] == ["durabilité"]


def test_a_scalar_where_a_list_belongs_is_refused():
    """The exact distinction a regex on the frontmatter cannot make."""
    problems = CAPABILITY_SCHEMA.errors({**VALID, "aliases": "durabilité"}, "cap1")
    assert any("aliases" in p and "liste attendue" in p for p in problems)


def test_an_unknown_field_is_an_error_not_a_shrug():
    problems = CAPABILITY_SCHEMA.errors({**VALID, "score": 2}, "cap1")
    assert any("score" in p and "inconnu" in p for p in problems)


def test_a_missing_field_is_named(tmp_path):
    text = FRONTMATTER.replace("capacite_num: 1\n", "")
    with pytest.raises(NoteError, match="capacite_num"):
        load(capability(tmp_path, text))


def test_every_broken_note_is_reported_not_just_the_first(tmp_path):
    """Fixing a folder blind, one file per run, is how a validator gets abandoned."""
    root = capability(tmp_path, FRONTMATTER.replace("capacite_num: 1", "capacite_num: nope"))
    capability(root, FRONTMATTER.replace("capacite_num: 1", "capacite_num: 99"),
               "cap2-descente.md")
    with pytest.raises(NoteError) as caught:
        load(root)
    assert "cap1-durabilite.md" in str(caught.value)
    assert "cap2-descente.md" in str(caught.value)


def test_the_real_analysis_notes_conform_without_operational_state():
    vault = VAULT_ROOT
    capabilities = load(vault)
    assert len(capabilities) == 6
    for slug, data in capabilities.items():
        assert data["slug"] == slug
        assert not ({"besoin", "score", "sur", "statut", "evalue_le",
                     "prochain_test", "verrou"} & data.keys())
