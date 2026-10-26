"""Ce que la MACHINE garde pour l'exploitation sans le donner à Sam.

Le 2026-08-21 Sam a reçu, sous en-tête technique, trois textos d'affilée pour UNE
seule panne : deux fois « une mise à jour du runtime attend un déploiement », puis
« commit enregistré localement mais push Git échoué ». Le doublon venait de
`_push()`, qui rejoue `sync()` avant de réessayer ; le troisième était la conséquence
du premier, pas un incident distinct ; et rien, côté transport, n'empêchait la même
panne de repartir au tour suivant.

Une panne interne n'appelle aucune action de l'athlète. Les alertes du dépôt sont
déjà dédupliquées tant qu'elles n'ont pas été drainées ; le bot les journalise sans
ajouter d'état durable ni envoyer de texto.
"""

from unittest.mock import MagicMock

from redline import vault as vault_module
from redline.vault import VaultAlert


def _alerts_sent(client) -> list[str]:
    return [call.args[1] for call in client.send_text.call_args_list]


def test_an_internal_incident_is_logged_without_text(make_bot, caplog):
    client = MagicMock()
    client.send_text.return_value.ok = True
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.drain_alerts.return_value = [
        VaultAlert(vault_module.ALERT_DEPLOY, "Une mise à jour du runtime attend "
                   "un déploiement : server/redline/bot.py")]
    bot = make_bot(client=client, repo=repo)

    bot._log_vault_alerts()

    assert _alerts_sent(client) == []
    assert caplog.text.count("Incident interne « vault:deploiement »") == 1


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
    assert "vault:deploiement" in caplog.text
    assert "vault:projection" in caplog.text
