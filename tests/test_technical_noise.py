"""What the MACHINE keeps for operations without giving it to the athlete.

One single outage must not produce several technical texts in a row: a duplicate came
from `_push()`, which replays `sync()` before retrying, a later one was the consequence
of the first rather than a distinct incident, and nothing on the transport side
prevented the same outage from going out again on the next turn.

An internal outage calls for no action from the athlete. The repository's alerts are
already deduplicated until drained; the bot logs them without adding durable state or
sending a text.
"""

from unittest.mock import MagicMock

from redline.service import vault as vault_module
from redline.service.vault import VaultAlert


def _alerts_sent(client) -> list[str]:
    return [call.args[1] for call in client.send_text.call_args_list]


def test_an_internal_incident_is_logged_without_text(make_bot, caplog):
    client = MagicMock()
    client.send_text.return_value.ok = True
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.drain_alerts.return_value = [
        VaultAlert(vault_module.ALERT_DEPLOY, "Une mise à jour du runtime attend "
                   "un déploiement : src/redline/service/bot.py")]
    bot = make_bot(client=client, repo=repo)

    bot._log_vault_alerts()

    assert _alerts_sent(client) == []
    assert caplog.text.count("Incident interne « vault:deployment »") == 1


def test_two_distinct_internal_causes_are_logged_without_text(make_bot, caplog):
    client = MagicMock()
    client.send_text.return_value.ok = True
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.drain_alerts.side_effect = [
        [VaultAlert(vault_module.ALERT_DEPLOY, "déploiement en attente"),
         VaultAlert(vault_module.ALERT_PROJECTION, "projection à reprendre")],
    ]
    bot = make_bot(client=client, repo=repo)

    bot._log_vault_alerts()

    assert _alerts_sent(client) == []
    assert "vault:deployment" in caplog.text
    assert "vault:projection" in caplog.text
