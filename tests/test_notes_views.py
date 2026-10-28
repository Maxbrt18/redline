"""Reading a note, and generating a view from it.

Both sides of the rule: the frontmatter is validated (never regexed), and the
Markdown that a human reads is produced from it (never copied by hand).
"""

import pytest

from redline.model import notes, views
from redline.model.schema import Schema, field

EXERCISE = Schema("exercice", [
    field("type", str, choices=("exercise",)),
    field("name", str),
    field("sets", int, required=False, minimum=1),
])


def note(tmp_path, text, name="calf-bent.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------- reading

def test_a_conforming_note_is_read(tmp_path):
    path = note(tmp_path, "---\ntype: exercise\nname: Mollet\nsets: 3\n---\n\nLe pourquoi.\n")
    assert notes.read(path, EXERCISE) == {"type": "exercise", "name": "Mollet", "sets": 3}


def test_the_body_is_never_part_of_the_fact(tmp_path):
    """The body holds prose and wikilinks; no code extracts a fact from it."""
    path = note(tmp_path, "---\ntype: exercise\nname: Mollet\n---\n\nsets: 99\n[[injury]]\n")
    assert notes.read(path, EXERCISE) == {"type": "exercise", "name": "Mollet"}
    assert "[[injury]]" in notes.split(path.read_text(encoding="utf-8"))[1]


def test_a_list_is_a_list_and_not_a_string(tmp_path):
    """The exact distinction a regex on the frontmatter cannot make."""
    schema = Schema("x", [field("targets", list, item=field("t", str))])
    path = note(tmp_path, "---\ntargets: [soleaire, gastro]\n---\n")
    assert notes.read(path, schema) == {"targets": ["soleaire", "gastro"]}


def test_a_nonconforming_note_names_the_file_and_every_problem(tmp_path):
    path = note(tmp_path, "---\ntype: exercise\nsets: 0\noops: 1\n---\n")
    with pytest.raises(notes.NoteError) as caught:
        notes.read(path, EXERCISE)
    message = str(caught.value)
    assert "calf-bent.md : note invalide" in message
    assert "champ « name » manquant" in message
    assert "champ « oops » inconnu" in message
    assert "champ « sets » : valeur 0, minimum 1" in message


def test_broken_yaml_is_a_readable_refusal_not_a_trace(tmp_path):
    path = note(tmp_path, "---\nname: [unclosed\n---\n")
    with pytest.raises(notes.NoteError) as caught:
        notes.frontmatter(path)
    assert "frontmatter illisible" in str(caught.value)
    assert "\n" not in str(caught.value)


def test_a_note_without_frontmatter_is_refused(tmp_path):
    with pytest.raises(notes.NoteError, match="pas de frontmatter"):
        notes.frontmatter(note(tmp_path, "# Juste de la prose\n"))


def test_a_missing_file_is_refused_by_name(tmp_path):
    with pytest.raises(notes.NoteError, match=r"absent\.md : illisible"):
        notes.frontmatter(tmp_path / "absent.md")


def test_read_all_keys_on_the_file_name(tmp_path):
    note(tmp_path, "---\ntype: exercise\nname: Mollet\n---\n", "calf-bent.md")
    note(tmp_path, "---\ntype: exercise\nname: Step-up\n---\n", "step-up.md")
    loaded = notes.read_all(tmp_path, EXERCISE)
    assert sorted(loaded) == ["calf-bent", "step-up"]
    assert loaded["step-up"]["slug"] == "step-up"


def test_read_all_reports_every_broken_note_not_the_first(tmp_path):
    note(tmp_path, "---\ntype: exercise\n---\n", "a.md")
    note(tmp_path, "---\ntype: exercise\nname: Bon\n---\n", "b.md")
    note(tmp_path, "---\ntype: nope\nname: X\n---\n", "c.md")
    with pytest.raises(notes.NoteError) as caught:
        notes.read_all(tmp_path, EXERCISE)
    assert "a.md" in str(caught.value) and "c.md" in str(caught.value)


# ------------------------------------------------------------------ the views

DOC = "Avant.\n\n<!-- EXOS_START -->\nvieux contenu\n<!-- EXOS_END -->\n\nAprès.\n"


def test_a_view_is_replaced_between_its_markers(tmp_path):
    path = note(tmp_path, DOC, "strength.md")
    assert views.apply(path, "EXOS", "nouveau") is True
    text = path.read_text(encoding="utf-8")
    assert "<!-- EXOS_START -->\nnouveau\n<!-- EXOS_END -->" in text
    assert text.startswith("Avant.") and text.rstrip().endswith("Après.")


def test_regenerating_an_up_to_date_view_changes_nothing(tmp_path):
    path = note(tmp_path, DOC, "strength.md")
    views.apply(path, "EXOS", "nouveau")
    before = path.read_text(encoding="utf-8")
    assert views.apply(path, "EXOS", "nouveau") is False
    assert path.read_text(encoding="utf-8") == before


def test_check_reports_the_drift_without_repairing_it(tmp_path):
    """A control that silently fixes what it should flag is not a control."""
    path = note(tmp_path, DOC, "strength.md")
    assert views.apply(path, "EXOS", "nouveau", check=True) is True
    assert "vieux contenu" in path.read_text(encoding="utf-8")


def test_content_with_a_backslash_survives(tmp_path):
    """re.sub would have read \\1 as a replacement group."""
    path = note(tmp_path, DOC, "strength.md")
    views.apply(path, "EXOS", r"une \1 et un \\ dans la prose")
    assert r"une \1 et un \\ dans la prose" in path.read_text(encoding="utf-8")


def test_missing_markers_say_which_ones_to_add(tmp_path):
    path = note(tmp_path, "Rien ici.\n", "strength.md")
    with pytest.raises(views.ViewDrift, match="EXOS_START"):
        views.apply(path, "EXOS", "x")


def test_table_escapes_the_one_character_that_breaks_the_grid():
    rendered = views.table(["Exercice", "Dose"], [["Mollet | fléchi", "3 × 8"]])
    assert rendered.splitlines() == [
        "| Exercice | Dose |",
        "|---|---|",
        "| Mollet \\| fléchi | 3 × 8 |",
    ]


def test_a_modified_note_is_reread_and_a_copy_never_leaks_into_the_cache(tmp_path):
    """The frontmatter is kept in memory as long as the file does not change on disk."""
    path = tmp_path / "note.md"
    path.write_text("---\nslug: a\ntags: [x]\n---\ncorps\n", encoding="utf-8")
    first = notes.frontmatter(path)
    first["tags"].append("muté")
    assert notes.frontmatter(path)["tags"] == ["x"]
    path.write_text("---\nslug: b\ntags: [y, z]\n---\ncorps\n", encoding="utf-8")
    assert notes.frontmatter(path) == {"slug": "b", "tags": ["y", "z"]}
