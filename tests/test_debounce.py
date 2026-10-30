"""Grouping of texts sent in a burst into a single Claude run.

Two clocks, which is what makes this fragile: we wait `DEBOUNCE_SECONDS` after the
LAST text (restarted on every new one), with a cap `DEBOUNCE_MAX_SECONDS` since the
FIRST. Both are documented options of `.env.example`. A regression here starts one
Claude run per text of a burst, or loses one.

We test `ready_buffer` rather than `next`: that is where the arithmetic lives, and it
keeps the suite from sleeping. `time.monotonic` is frozen, so no test depends on the
speed of the machine running it.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from conftest import FakeAgent


@pytest.fixture
def bot(make_bot):
    return make_bot(debounce_seconds=5, debounce_max_seconds=20)


def at(moment):
    """Freezes the module clock: the debounce reads `time.monotonic`."""
    return patch("redline.service.work_queue.time.monotonic", return_value=moment)


def test_a_lone_text_waits_out_the_quiet_delay(bot):
    with at(100.0):
        bot.submit_inbound("4242", "salut")
    with at(103.0):
        psid, wait = bot._queue.ready_buffer()
    assert psid is None
    assert wait == pytest.approx(2.0)          # 100 + 5 - 103


def test_it_ripens_once_the_quiet_delay_has_passed(bot):
    with at(100.0):
        bot.submit_inbound("4242", "salut")
    with at(105.0):
        assert bot._queue.ready_buffer() == ("4242", None)


def test_a_new_text_pushes_the_deadline_back(bot):
    """The core of the debounce: three texts in a row are not three runs."""
    with at(100.0):
        bot.submit_inbound("4242", "j'ai couru")
    with at(104.0):
        bot.submit_inbound("4242", "6 km")
        assert bot._queue.ready_buffer()[0] is None   # the deadline moved back to 109
    with at(108.0):
        assert bot._queue.ready_buffer()[0] is None
    with at(109.0):
        assert bot._queue.ready_buffer() == ("4242", None)


def test_the_burst_is_delivered_as_one_message(bot):
    with at(100.0):
        bot.submit_inbound("4242", "j'ai couru")
        bot.submit_inbound("4242", "6 km")
        bot.submit_inbound("4242", "ça allait")
    with at(105.0):
        request = bot._queue.next()
    assert request.psid == "4242"
    assert request.text == "j'ai couru\n6 km\nça allait"


def test_the_cap_wins_over_a_never_ending_burst(bot):
    """Without a cap, someone typing a line every 4 s never gets a reply: each text
        would push the deadline back indefinitely."""
    moment = 100.0
    for _ in range(10):                     # a text every 4 s, without a pause
        with at(moment):
            bot.submit_inbound("4242", "encore")
        moment += 4.0
    with at(120.0):                         # 100 + debounce_max_seconds
        assert bot._queue.ready_buffer() == ("4242", None)


def test_an_empty_queue_asks_to_sleep_indefinitely(bot):
    assert bot._queue.ready_buffer() == (None, None)


def test_the_nearest_deadline_drives_the_wait(bot):
    """The returned delay is used as the timeout of `Condition.wait`: taking it from the
        wrong buffer would make the worker sleep past an already ripe reply."""
    with at(100.0):
        bot.submit_inbound("4242", "premier")
    with at(102.0):
        bot.submit_inbound("9999", "second")
    with at(103.0):
        _, wait = bot._queue.ready_buffer()
    assert wait == pytest.approx(2.0)       # 4242 ripens at 105, 9999 at 107


def test_the_wait_is_never_zero(bot):
    """A zero timeout would make the worker spin in a tight CPU loop."""
    with at(100.0):
        bot.submit_inbound("4242", "salut")
    with at(104.999999):
        _, wait = bot._queue.ready_buffer()
    assert wait >= 0.05


def test_status_bypasses_both_debounce_and_a_busy_worker(bot):
    from unittest.mock import MagicMock

    delivered = __import__("threading").Event()
    bot._run_command = MagicMock(side_effect=lambda *args, **kwargs: delivered.set() or True)
    bot._state.claim_inbound("mid-status", "4242", "text", "/status")

    bot.submit_inbound("4242", "/status", message_id="mid-status")

    assert delivered.wait(1)
    bot._run_command.assert_called_once_with(
        "4242", "/status", message_ids=("mid-status",))
    assert not bot.has_pending_inbound()


def test_a_pending_database_message_is_recovered_on_start(make_bot, store):
    store.claim_inbound("m-recover", "4242", "text", "message avant crash")
    agent = FakeAgent()
    bot = make_bot(agent=agent, debounce_seconds=0, debounce_max_seconds=0)

    bot.start()
    deadline = time.time() + 1
    while store.pending_inbound() and time.time() < deadline:
        time.sleep(0.01)
    bot.stop()

    assert [call["message"] for call in agent.calls]
    assert store.pending_inbound() == []


def test_a_pending_attachment_keeps_its_kind_after_restart(make_bot, store):
    from redline.service.attachments import encode_payload
    from redline.service.messenger import DeliveryResult

    raw = encode_payload([{"type": "video", "payload": {
        "url": "https://lookaside.fbsbx.com/v.mp4"}}])
    store.claim_inbound("photo-recover", "4242", "attachment", raw)
    client = MagicMock()
    client.send_burst.return_value = DeliveryResult.success()
    client.action.return_value = DeliveryResult.success()
    bot = make_bot(client=client)

    bot.start()
    deadline = time.time() + 1
    while store.pending_inbound() and time.time() < deadline:
        time.sleep(0.01)
    bot.stop()

    client.send_burst.assert_called_once()
    assert "vidéos" in client.send_burst.call_args.args[1]
    assert store.pending_inbound() == []


def test_a_failed_direct_message_is_retried_and_stays_durable(make_bot, store):
    from redline.service.agent import RunResult
    from redline.service.messenger import DeliveryResult

    store.claim_inbound("m-retry", "4242", "text", "message à ne pas perdre")
    from redline.service.agent import FailureKind
    agent = FakeAgent(result=RunResult(
        error="moteur indisponible", failure=FailureKind.PREFLIGHT))
    client = MagicMock()
    client.send_text.return_value = DeliveryResult.success()
    client.send_burst.return_value = DeliveryResult.success()
    client.action.return_value = DeliveryResult.success()
    bot = make_bot(
        agent=agent, client=client, debounce_seconds=0, debounce_max_seconds=0)
    # An immediate retry, then the next one out of the test's reach: with (0.01,) alone,
    # a third pass could land before `stop()` (6 calls instead of 4).
    bot.REACTIVE_RETRY_DELAYS = (0.01, 60.0)  # < OUTAGE_NOTICE_AFTER: no notice

    bot.start()
    deadline = time.time() + 1
    while len(agent.calls) < 4 and time.time() < deadline:
        time.sleep(0.01)
    bot.stop()

    # Two route profiles on each pass, then an application-level retry.
    assert len(agent.calls) == 4
    assert store.pending_inbound()[0]["message_id"] == "m-retry"
    client.send_text.assert_not_called()
    client.send_burst.assert_not_called()
