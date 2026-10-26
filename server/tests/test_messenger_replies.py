"""Le swipe-reply est une relation explicite, pas une ressemblance de texte."""

from conftest import FakeAgent

from redline.agent import RunResult


def test_the_replied_message_is_injected_next_to_the_current_text(make_bot, store):
    store.record_outbound(
        "4242", "Tu ne manques pas de volonté ; tu protèges une certitude.",
        source_key="test:old-coach",
        platform_messages=((
            "m-old-coach", "Tu ne manques pas de volonté ; tu protèges une certitude."),))
    store.claim_inbound(
        "m-current", "4242", "text", "Oui. Et je le vois maintenant.",
        reply_to_message_id="m-old-coach")
    agent = FakeAgent(result=RunResult(full_text="On tient le fil."), depth="N0")
    bot = make_bot(agent=agent)

    bot._respond(
        "4242", "Oui. Et je le vois maintenant.", message_ids=("m-current",))

    prompt = agent.calls[-1]["message"]
    assert "RÉPONSE MESSENGER CIBLÉE" in prompt
    assert "Tu ne manques pas de volonté" in prompt
    assert prompt.rstrip().endswith("Oui. Et je le vois maintenant.")


def test_an_unknown_old_target_is_signalled_without_inventing_it(make_bot, store):
    store.claim_inbound(
        "m-current", "4242", "text", "C'est ça.",
        reply_to_message_id="m-before-retention")
    agent = FakeAgent(result=RunResult(full_text="Je te suis."), depth="N0")
    bot = make_bot(agent=agent)

    bot._respond("4242", "C'est ça.", message_ids=("m-current",))

    prompt = agent.calls[-1]["message"]
    assert "référence inconnue" in prompt
    assert "m-before-retention" not in prompt
