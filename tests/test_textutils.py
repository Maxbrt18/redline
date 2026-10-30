"""strip_links: the only mechanical rewrite allowed on the coach's output."""

from redline.service.textutils import strip_links


def test_wikilink_with_alias():
    assert strip_links("regarde [[current-plan|ton plan]] demain") == "regarde ton plan demain"


def test_wikilink_without_alias():
    assert strip_links("c'est dans [[pain-log]]") == "c'est dans pain-log"


def test_markdown_link():
    assert strip_links("vois [ta sortie](https://strava.com/x) ici") == "vois ta sortie ici"


def test_normal_prose_intact():
    text = "Tu as couru 12 km à 5:30/km, FC 152. Bravo [sincèrement] !"
    assert strip_links(text) == text
