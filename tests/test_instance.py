"""`redline.toml`: what distinguishes an instance, typed and closed by default."""

import pytest
from conftest import ENGINE_ROOT, TEST_VAULT

from redline.model import instance


def test_the_fixture_instance_loads():
    loaded = instance.load(TEST_VAULT)
    assert loaded.athlete_name == "Sam"
    assert loaded.capability_label("cap5-injury") == "Injury"


def test_an_undeclared_capability_still_gets_a_readable_label():
    assert instance.load(TEST_VAULT).capability_label("cap9-chaleur") == "Chaleur"


def test_the_shipped_example_is_refused_until_filled():
    """The shipped template must match the schema, and be rejected only for its `[…]`."""
    with pytest.raises(instance.InstanceError) as refused:
        instance.parse((ENGINE_ROOT / instance.EXAMPLE).read_text(encoding="utf-8"))
    message = str(refused.value)
    assert "encore à remplir" in message
    assert "athlete.name" in message and "notebook.place" in message
    assert "athlete.hr_rest" in message and "athlete.hr_max" in message
    assert "athlete.timezone" in message
    assert "inconnu" not in message


def test_the_time_zone_is_declared_and_checked():
    text = (TEST_VAULT / instance.FILENAME).read_text(encoding="utf-8")
    assert instance.parse(text).timezone.key == "Europe/Paris"
    with pytest.raises(instance.InstanceError, match="fuseau inconnu « Mars/Olympus »"):
        instance.parse(text.replace('"Europe/Paris"', '"Mars/Olympus"'))
    with pytest.raises(instance.InstanceError, match="timezone"):
        instance.parse(text.replace('timezone = "Europe/Paris"\n', ""))


def test_the_local_time_zone_is_the_one_the_instance_declares():
    from redline.model import local_time

    assert local_time.zone().key == "Europe/Paris"


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


def test_heart_rates_give_the_karvonen_bounds():
    """Heart-rate zones are computed from the instance's HR values, never from engine
    values: the top of Z2 (`hr_bounds[1]`) caps easy runs."""
    sam = instance.load(TEST_VAULT)
    assert (sam.hr_rest, sam.hr_max) == (55, 190)
    assert sam.hr_bounds == (136, 149, 163, 176)



def test_the_process_clock_follows_the_instance_zone(monkeypatch):
    import os
    import time

    from redline.model import local_time
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        local_time.apply_to_process()
        assert os.environ["TZ"] == "Europe/Paris"
        assert time.tzname[0] in {"CET", "CEST"}
    finally:
        monkeypatch.undo()
        time.tzset()
