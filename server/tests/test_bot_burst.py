"""La réponse du coach atteint Sam en bulles, jamais avec un `---` littéral.

Le 2026-08-05, une réponse N2 est arrivée avec le trait `---` visible : le chemin
non-N0 envoyait `full_text` par `send_text`, qui ne découpe que sur la longueur et
laissait passer le séparateur de burst. Ce test pince le branchement -- le seul
chemin qui atteint Sam pour une réponse ordinaire doit passer par `send_burst`,
là où le `---` devient deux textos.
"""

from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.agent import RunResult
from redline.messenger import MessengerClient


def test_a_reply_with_a_separator_goes_out_bursted(make_bot):
    client = MagicMock()
    reply = "On compte sur les 7 km.\n\n---\n\nLes 9, c'est la marge, pas la cible."
    bot = make_bot(client=client,
                   agent=FakeAgent(result=RunResult(full_text=reply), depth="N2"))
    bot._respond("4242", "pourquoi 9 ?")

    # La réponse part par send_burst (pas send_text), et aucun texto ne contient `---`.
    client.send_burst.assert_called_once()
    assert client.send_burst.call_args.args[1] == reply
    parts = MessengerClient._burst_parts(reply)
    assert len(parts) == 2 and all("---" not in p for p in parts)
