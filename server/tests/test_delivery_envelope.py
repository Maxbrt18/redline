from unittest.mock import MagicMock, call

from redline.delivery import DeliveryService, EnvelopeBuilder
from redline.messenger import DeliveryResult
from redline.outbox import Outbox


def service(tmp_path, store):
    client = MagicMock()
    client.send_burst.return_value = DeliveryResult.success()
    return DeliveryService(client, Outbox(tmp_path / "outbox"), store, "https://coach.test")


def test_a_delivered_envelope_is_never_replayed(tmp_path, store):
    delivery = service(tmp_path, store)
    envelope = EnvelopeBuilder("4242", "proactive:stable").text("Bonjour").build()

    first = delivery.deliver(envelope)
    second = delivery.deliver(envelope)

    assert first.text_ok and first.attachments_ok
    assert second.text_ok and second.attachments_ok
    assert second.replayed and not second.uncertain
    delivery.client.send_burst.assert_called_once()


def test_a_crash_ambiguous_boundary_prefers_at_most_once(tmp_path, store):
    delivery = service(tmp_path, store)
    envelope = EnvelopeBuilder("4242", "proactive:crash").text("Bonjour").build()
    store.begin_outbound_delivery(
        envelope.source_key, envelope.psid, delivery._digest(envelope))

    receipt = delivery.deliver(envelope)

    assert receipt.replayed and receipt.uncertain
    delivery.client.send_burst.assert_not_called()


def test_a_zero_delivery_failure_can_be_retried(tmp_path, store):
    delivery = service(tmp_path, store)
    envelope = EnvelopeBuilder("4242", "proactive:retry").text("Bonjour").build()
    delivery.client.send_burst.side_effect = [
        DeliveryResult.failure("réseau", delivered=0), DeliveryResult.success()]

    assert not delivery.deliver(envelope).text_ok
    assert delivery.deliver(envelope).text_ok
    assert delivery.client.send_burst.call_count == 2


def test_a_failed_delivery_can_retry_a_revised_payload(tmp_path, store):
    delivery = service(tmp_path, store)
    first = EnvelopeBuilder("4242", "proactive:revised-retry").text(
        "Version initiale").build()
    second = EnvelopeBuilder("4242", first.source_key).text(
        "Version corrigée").build()
    delivery.client.send_burst.side_effect = [
        DeliveryResult.failure("réseau", delivered=0), DeliveryResult.success()]

    assert not delivery.deliver(first).text_ok
    assert delivery.deliver(second).text_ok
    delivery.client.send_burst.assert_has_calls([
        call("4242", "Version initiale", messaging_type="RESPONSE", tag=None),
        call("4242", "Version corrigée", messaging_type="RESPONSE", tag=None),
    ])


def test_a_delivered_envelope_is_never_sent_twice_even_if_recomputed(tmp_path, store):
    """RWF-005 : une reprise qui recalculerait sa réponse retrouve l'entrant déjà servi
    et l'acquitte, au lieu de boucler sur une collision à chaque tentative."""
    delivery = service(tmp_path, store)
    first = EnvelopeBuilder("4242", "proactive:delivered-collision").text(
        "Version initiale").build()
    revised = EnvelopeBuilder("4242", first.source_key).text(
        "Version corrigée").build()

    assert delivery.deliver(first).text_ok
    second = delivery.deliver(revised)
    assert second.replayed and second.text_ok
    delivery.client.send_burst.assert_called_once()


def test_a_computed_envelope_is_stored_and_resent_as_is(tmp_path, store):
    """Calculer une fois, livrer plusieurs fois : le texte stocké repart sans le modèle."""
    from redline.messenger import DeliveryResult

    delivery = service(tmp_path, store)
    envelope = (EnvelopeBuilder("4242", "reactive:m1").meta(memory="Réponse", turn_id="t")
                .text("Réponse").text("Programme", burst=False, pause_before=True).build())
    delivery.client.send_burst.return_value = DeliveryResult.failure("Send API 500")

    assert not delivery.deliver(envelope).delivered_any
    stored = delivery.stored("reactive:m1")
    assert stored.status == "failed" and stored.resendable
    assert stored.meta == {"memory": "Réponse", "turn_id": "t"}

    delivery.client.send_burst.return_value = DeliveryResult.success()
    delivery.client.send_text.return_value = DeliveryResult.success()
    assert delivery.resume(stored).text_ok
    assert delivery.client.send_text.call_args.args[1] == "Programme"
    assert delivery.stored("reactive:m1").status == "delivered"


def test_a_prepared_envelope_waits_without_being_sent(tmp_path, store):
    delivery = service(tmp_path, store)
    envelope = EnvelopeBuilder("4242", "proactive:morning:1").text("Bonjour").build()

    assert delivery.prepare(envelope)
    assert delivery.stored("proactive:morning:1").status == "prepared"
    delivery.client.send_burst.assert_not_called()
    assert delivery.resume(delivery.stored("proactive:morning:1")).text_ok
    assert not delivery.prepare(envelope)      # déjà parti : plus rien à préparer
