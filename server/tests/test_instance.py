"""`redline.toml` : ce qui distingue une instance, typé et fermé par défaut."""

import pytest
from conftest import ENGINE_ROOT, TEST_VAULT

from scripts.model import instance


def test_the_fixture_instance_loads():
    loaded = instance.load(TEST_VAULT)
    assert loaded.athlete_name == "Sam"
    assert loaded.capability_label("cap5-injury") == "Injury"


def test_an_undeclared_capability_still_gets_a_readable_label():
    assert instance.load(TEST_VAULT).capability_label("cap9-chaleur") == "Chaleur"


def test_the_shipped_example_is_refused_until_filled():
    """Le gabarit livré doit être conforme au schéma, et refusé pour ses seuls `[…]`."""
    with pytest.raises(instance.InstanceError) as refused:
        instance.parse((ENGINE_ROOT / instance.EXAMPLE).read_text(encoding="utf-8"))
    message = str(refused.value)
    assert "encore à remplir" in message
    assert "athlete.name" in message and "notebook.place" in message
    assert "inconnu" not in message


def test_a_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(instance.InstanceError, match=instance.EXAMPLE):
        instance.load(tmp_path)


@pytest.mark.parametrize("text, expected", [
    ("[athlete]\nname = 'A'\nnickname = 'B'\n", "nickname"),
    ("[athlete\n", "illisible"),
])
def test_unknown_or_broken_input_is_refused(text, expected):
    with pytest.raises(instance.InstanceError, match=expected):
        instance.parse(text)


def test_an_athlete_without_injury_declares_none():
    text = (TEST_VAULT / instance.FILENAME).read_text(encoding="utf-8")
    text = text.replace('[[injuries]]\nslug = "injury"\nlabel = "Injury"\n', "")
    assert instance.parse(text).injuries == ()


def test_several_zones_are_kept_in_their_declared_order():
    text = (TEST_VAULT / instance.FILENAME).read_text(encoding="utf-8")
    text += '\n[[injuries]]\nslug = "genou"\nlabel = "Genou"\n'
    assert [zone.slug for zone in instance.parse(text).injuries] == ["injury", "genou"]


def test_a_zone_declared_twice_is_refused():
    text = (TEST_VAULT / instance.FILENAME).read_text(encoding="utf-8")
    text += '\n[[injuries]]\nslug = "injury"\nlabel = "Encore"\n'
    with pytest.raises(instance.InstanceError, match="double"):
        instance.parse(text)


def test_a_duplicated_capability_is_refused():
    text = (TEST_VAULT / instance.FILENAME).read_text(encoding="utf-8")
    text += '\n[[capabilities]]\nslug = "cap1-durabilite"\nlabel = "Encore"\n'
    with pytest.raises(instance.InstanceError, match="double"):
        instance.parse(text)
