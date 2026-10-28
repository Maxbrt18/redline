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


def test_delivery_issues_the_capability_only_after_the_image_is_delivered(make_bot, store):
    from unittest.mock import MagicMock

    from redline.service.messenger import DeliveryResult

    image = MagicMock()
    type(image).name = "strength-2026-08-22.png"
    outbox = MagicMock()
    outbox.collect_new.return_value = [image]
    outbox.link_for.return_value = ("Renfo A", SESSION)
    client = MagicMock()
    client.send_image.return_value = DeliveryResult.success()
    client.send_button.return_value = DeliveryResult.success()
    bot = make_bot(client=client, outbox=outbox)

    from redline.service.delivery import EnvelopeBuilder

    envelope = EnvelopeBuilder("4242", "test:strength-card")
    envelope.images(outbox, {})
    assert bot._delivery.deliver(envelope.build()).attachments_ok
    client.send_image.assert_called_once()
    button = client.send_button.call_args.args
    assert button[0:2] == ("4242", "Renfo A")
    assert button[2].startswith("https://coach.test/s#t=")
    token = button[2].split("#t=", 1)[1]
    resolved = store.resolve_strength_link("4242", token)
    assert resolved["status"] == "active"
    assert resolved["session"]["exercises"] == SESSION["exercises"]
    outbox.archive.assert_called_once_with(image)


def test_a_failed_button_revokes_its_exact_capability(make_bot, store):
    from unittest.mock import MagicMock

    from redline.service.messenger import DeliveryResult

    image = MagicMock()
    type(image).name = "strength.png"
    outbox = MagicMock()
    outbox.collect_new.return_value = [image]
    outbox.link_for.return_value = ("Renfo A", SESSION)
    client = MagicMock()
    client.send_image.return_value = DeliveryResult.success()
    client.send_button.return_value = DeliveryResult.failure("Meta indisponible")
    bot = make_bot(client=client, outbox=outbox)

    from redline.service.delivery import EnvelopeBuilder

    envelope = EnvelopeBuilder("4242", "test:failed-strength-card")
    envelope.images(outbox, {})
    assert not bot._delivery.deliver(envelope.build()).attachments_ok
    assert store._deliveries["test:failed-strength-card"]["status"] == "delivered"
    token = client.send_button.call_args.args[2].split("#t=", 1)[1]
    assert store.resolve_strength_link("4242", token)["status"] == "revoked"


# ------------------------------------------------ le résidu d'un tour mort (18/08)

def test_leftovers_sees_what_no_snapshot_will_ever_collect(tmp_path):
    """⭐ 2026-08-18. `collect_new` compare à un instantané pris au DÉBUT du tour :
    une image laissée par un tour mort figure dans tous les instantanés suivants, donc
    aucun ne la collecte. `strength-2026-08-13.png` et son lien dormaient dans l'outbox
    de prod depuis le 2026-08-14 08 h 24 — une carte de renfo entièrement générée que
    Sam n'a jamais reçue et dont rien n'a jamais parlé."""
    root = tmp_path / "outbox"
    outbox = Outbox(root)
    orphan = root / "strength-2026-08-13.png"
    orphan.write_bytes(b"png")
    strength.write_sidecar(orphan, "Renfo A", SESSION)

    # Le tour suivant ne la voit pas : elle est déjà dans son instantané de départ.
    assert outbox.collect_new(outbox.snapshot()) == []
    # Au démarrage, si : aucun tour n'est en vol, donc tout ce qui traîne est un résidu.
    assert outbox.leftovers() == [orphan]


def test_leftovers_is_empty_on_a_clean_outbox(tmp_path):
    assert Outbox(tmp_path / "outbox").leftovers() == []


def test_the_bot_archives_the_residue_without_waking_the_athlete(make_bot, caplog):
    """Une carte de séance qui arrive quatre jours plus tard est pire qu'absente : elle
    s'archive et l'omission reste dans le journal d'exploitation."""
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
    assert "Outbox : 1 image(s) laissée(s)" in caplog.text


def test_a_clean_startup_says_nothing(make_bot):
    from unittest.mock import MagicMock

    client = MagicMock()
    bot = make_bot(client=client, allowed_psid="4242")
    assert bot.reconcile_outbox() == 0
    client.send_text.assert_not_called()
