"""A coach reply reaches the athlete as bubbles, never with a literal `---`.

The non-N0 path used to send `full_text` through `send_text`, which only splits on
length and lets the burst separator through. This test pins the branch: the only path
that reaches the athlete for an ordinary reply must go through `send_burst`, where
`---` becomes two texts.
"""

from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.messenger import MessengerClient


def test_a_reply_with_a_separator_goes_out_bursted(make_bot):
    client = MagicMock()
    reply = "On compte sur les 7 km.\n\n---\n\nLes 9, c'est la marge, pas la cible."
    bot = make_bot(client=client,
                   agent=FakeAgent(result=RunResult(full_text=reply), depth="N2"))
    bot._respond("4242", "pourquoi 9 ?")

    # The reply goes out through send_burst (not send_text), and no text contains `---`.
    client.send_burst.assert_called_once()
    assert client.send_burst.call_args.args[1] == reply
    parts = MessengerClient._burst_parts(reply)
    assert len(parts) == 2 and all("---" not in p for p in parts)
