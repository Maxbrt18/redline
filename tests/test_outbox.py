"""Outbox: detecting images produced during a run, archiving, pruning."""

import os
import time

from redline.service import strength
from redline.service.outbox import KEEP_SENT, Outbox

SESSION = {"sid": "strength-2026-08-22-a", "session": "a",
           "exercises": [{"slug": "step-up", "name": "Step-up"}]}


def test_collect_only_sees_the_new(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    old = tmp_path / "outbox" / "old.png"
    old.write_bytes(b"png")

    before = outbox.snapshot()
    fresh = tmp_path / "outbox" / "workout-1.png"
    fresh.write_bytes(b"png")
    (tmp_path / "outbox" / "notes.txt").write_text("not an image")

    assert outbox.collect_new(before) == [fresh]


def test_replacing_an_existing_path_is_a_new_card(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    image = tmp_path / "outbox" / "strength.png"
    image.write_bytes(b"old")
    before = outbox.snapshot()

    image.write_bytes(b"new-card")

    assert outbox.collect_new(before) == [image]


def test_collect_chronological_order(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    before = outbox.snapshot()
    second = tmp_path / "outbox" / "b.png"
    first = tmp_path / "outbox" / "a.png"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    now = time.time()
    os.utime(first, (now - 10, now - 10))
    os.utime(second, (now, now))
    assert outbox.collect_new(before) == [first, second]


def test_archive_moves_and_prunes(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    for index in range(KEEP_SENT + 3):
        image = tmp_path / "outbox" / f"img-{index:02d}.png"
        image.write_bytes(b"png")
        outbox.archive(image)
    sent = list((tmp_path / "outbox" / "sent").iterdir())
    assert len(sent) == KEEP_SENT
    assert not list((tmp_path / "outbox").glob("*.png"))  # the root is empty


# ---- the session link riding along with a card

def test_sidecar_is_not_mistaken_for_an_image(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    before = outbox.snapshot()
    image = tmp_path / "outbox" / "strength.png"
    image.write_bytes(b"png")
    strength.write_sidecar(image, "Renfo A", SESSION)
    assert outbox.collect_new(before) == [image]
    assert outbox.link_for(image) == ("Renfo A", SESSION)


def test_card_without_a_sidecar_has_no_link(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    image = tmp_path / "outbox" / "week.png"
    image.write_bytes(b"png")
    assert outbox.link_for(image) is None


def test_archive_takes_the_sidecar_with_it(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    image = tmp_path / "outbox" / "strength.png"
    image.write_bytes(b"png")
    strength.write_sidecar(image, "Renfo A", SESSION)
    outbox.archive(image)
    sent = tmp_path / "outbox" / "sent"
    assert (sent / "strength.png").exists()
    assert (sent / "strength.png.link").exists()
    assert not list((tmp_path / "outbox").glob("*.link"))


def test_failed_attachment_is_quarantined_with_its_sidecar(tmp_path):
    outbox = Outbox(tmp_path / "outbox")
    image = tmp_path / "outbox" / "strength.png"
    image.write_bytes(b"png")
    strength.write_sidecar(image, "Renfo A", SESSION)

    outbox.fail(image)

    failed = tmp_path / "outbox" / "failed"
    assert (failed / "strength.png").exists()
    assert (failed / "strength.png.link").exists()


def test_pruning_counts_cards_not_files(tmp_path):
    """A sidecar must not halve the kept history -- and must not outlive its
    card either."""
    outbox = Outbox(tmp_path / "outbox")
    for index in range(KEEP_SENT + 3):
        image = tmp_path / "outbox" / f"img-{index:02d}.png"
        image.write_bytes(b"png")
        strength.write_sidecar(image, "x", SESSION)
        outbox.archive(image)
    sent = tmp_path / "outbox" / "sent"
    assert len(list(sent.glob("*.png"))) == KEEP_SENT
    assert len(list(sent.glob("*.link"))) == KEEP_SENT


def test_a_strength_card_goes_out_alone(make_bot):
    """The app opens the session from Today: the card carries no link and mints no
    capability."""
    from unittest.mock import MagicMock

    from redline.service.delivery import EnvelopeBuilder
    from redline.service.messenger import DeliveryResult

    image = MagicMock()
    type(image).name = "strength-2026-08-22.png"
    outbox = MagicMock()
    outbox.collect_new.return_value = [image]
    outbox.link_for.return_value = ("Renfo A", SESSION)
    client = MagicMock()
    client.send_image.return_value = DeliveryResult.success()
    bot = make_bot(client=client, outbox=outbox)
    bot._delivery.state = MagicMock(wraps=bot._delivery.state)

    envelope = EnvelopeBuilder("4242", "test:strength-card")
    envelope.images(outbox, {})
    assert bot._delivery.deliver(envelope.build()).attachments_ok
    client.send_image.assert_called_once()
    assert client.method_calls == [call for call in client.method_calls
                                   if call[0] == "send_image"]
    bot._delivery.state.issue_strength_link.assert_not_called()
    outbox.archive.assert_called_once_with(image)


# ------------------------------------------------ residue of a dead turn

def test_leftovers_sees_what_no_snapshot_will_ever_collect(tmp_path):
    """`collect_new` compares against a snapshot taken at the START of the turn: an
        image left by a dead turn appears in every following snapshot, so none collects
        it. A fully generated strength card would sit in the outbox, never delivered and
        never reported."""
    root = tmp_path / "outbox"
    outbox = Outbox(root)
    orphan = root / "strength-2026-08-13.png"
    orphan.write_bytes(b"png")
    strength.write_sidecar(orphan, "Renfo A", SESSION)

    # The next turn does not see it: it is already in its starting snapshot.
    assert outbox.collect_new(outbox.snapshot()) == []
    # At startup it does: no turn is in flight, so everything lying around is residue.
    assert outbox.leftovers() == [orphan]


def test_leftovers_is_empty_on_a_clean_outbox(tmp_path):
    assert Outbox(tmp_path / "outbox").leftovers() == []


def test_the_bot_archives_the_residue_without_waking_the_athlete(make_bot, caplog):
    """A session card that arrives four days later is worse than absent: it is
        archived and the omission stays in the operations log."""
    from unittest.mock import MagicMock

    residue = MagicMock()
    type(residue).name = "strength-2026-08-13.png"
    outbox = MagicMock()
    outbox.snapshot.return_value = set()
    outbox.collect_new.return_value = []
    outbox.leftovers.return_value = [residue]

    client = MagicMock()
    from redline.service.messenger import DeliveryResult
    client.send_text.return_value = DeliveryResult.success()

    bot = make_bot(client=client, outbox=outbox, allowed_psid="4242")
    assert bot.reconcile_outbox() == 1

    outbox.archive.assert_called_once_with(residue)
    client.send_image.assert_not_called()
    client.send_text.assert_not_called()
    assert "Outbox: 1 image(s) left" in caplog.text


def test_a_clean_startup_says_nothing(make_bot):
    from unittest.mock import MagicMock

    client = MagicMock()
    bot = make_bot(client=client, allowed_psid="4242")
    assert bot.reconcile_outbox() == 0
    client.send_text.assert_not_called()
