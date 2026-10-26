from pathlib import Path

from scripts.model import knowledge_search


def test_search_finds_a_relevant_concept_with_a_readable_provenance(monkeypatch, tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "episodes").mkdir()
    (tmp_path / "concepts" / "shin-pain.md").write_text(
        "---\naliases: [blessure]\n---\n# Douleur injury\n"
        "La douleur injury doit être située dans le temps.\n")
    (tmp_path / "episodes" / "other.md").write_text("# Autre chose\n")
    monkeypatch.setattr(knowledge_search, "ROOT", Path(tmp_path))
    found = knowledge_search.search("blessure")
    assert found[0]["path"] == "concepts/shin-pain.md"
    assert found[0]["line"] == 2


def test_search_requires_meaningful_terms():
    assert knowledge_search.words("comment faire avec") == set()
