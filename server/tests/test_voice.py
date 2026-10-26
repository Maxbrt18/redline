"""Le coach relit ses messages sans ses tics (audit du 2026-10-11)."""

from redline.relationship import render_memory
from scripts.model import voice


def test_the_coach_rereads_itself_without_emoji_nor_opening():
    assert voice.muted("Salut Sam, c'est le grand jour. Sortie longue 🙂") == (
        "C'est le grand jour. Sortie longue")
    assert voice.muted("Légère, ça passe : tu y vas. 🙂  Je n'ai pas pu") == (
        "Légère, ça passe : tu y vas. Je n'ai pas pu")


def test_a_bare_greeting_or_reaction_keeps_a_readable_line():
    assert voice.muted("Hey") == "Hey"
    assert voice.muted("🙂") == "(réaction sans texte)"


def test_memory_mutes_the_coach_and_never_alex():
    memory = render_memory([
        {"role": "athlete", "body": "Légère 🙂", "id": "a"},
        {"role": "coach", "body": "Salut Sam, noté. 🙂", "id": "b"},
    ], [])
    assert "Sam [date inconnue; message_id=a]: Légère 🙂" in memory
    assert "Coach [date inconnue; message_id=b]: Noté." in memory
