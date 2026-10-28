"""L'angle d'un message proactif a un champ — donc plus rien à faire dans la prose.

Le 2026-08-21, le débrief Strava est parti chez Sam en commençant par « Angle non
répété jusqu'ici (dernier debrief utilisait "plaisir/effort" le 17/08). Ici je vais
partir du titre Strava et demander un récit libre sur l'orage. » — la machinerie du
coach, récitée à voix haute, pour la TROISIÈME fois après le 2026-08-19.

Les deux correctifs précédents étaient des durcissements de `STYLE_PROMPT`. Ils ne
pouvaient pas tenir : le déclencheur exigeait « un angle que tu n'as pas déjà servi »,
`fact-read.py proactive` était censé les lister, et **rien ne les a jamais écrits** —
`record_proactive_delivery` posait `angle: None` en dur. Le modèle devait donc
reconstituer les angles servis à partir des résumés bruts, et n'ayant aucun champ où
déposer ce raisonnement, il l'a déposé dans le message.

⭐ Règle de méta du 2026-08-06, appliquée telle quelle : **un garde-fou qui n'ouvre pas
de chemin correct fabrique son contournement.** Le chemin correct s'ouvre ici.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import load_script

from redline.model import commands
from redline.model.errors import InputError
from redline.service.prompts import PROACTIVE_TRIGGERS, build_proactive_prompt
from redline.service.vault import VaultRepo

fact_write = load_script("fact-write")


def _job():
    return SimpleNamespace(kind="strava_debrief", job_id="job-1", detail="90000000010")


def test_the_delivered_trace_carries_the_angle_the_turn_deposited():
    store = MagicMock()
    store.turn_angle.return_value = "titre Strava, récit libre"
    repo = VaultRepo("/tmp/vault", "origin", "main", operational_store=store)

    repo.record_proactive_delivery(_job(), datetime.now().astimezone(), "Alors, « La douche »…",
                                   "turn-42")

    store.turn_angle.assert_called_once_with("turn-42")
    assert store.add_proactive_message.call_args.args[0]["angle"] == "titre Strava, récit libre"


def test_a_message_without_an_angle_records_none_rather_than_an_empty_string():
    """`angle` est nullable : « pas d'angle » se lit NULL, jamais chaîne vide."""
    store = MagicMock()
    store.turn_angle.return_value = ""
    repo = VaultRepo("/tmp/vault", "origin", "main", operational_store=store)

    repo.record_proactive_delivery(_job(), datetime.now().astimezone(), "texte", "turn-42")

    assert store.add_proactive_message.call_args.args[0]["angle"] is None


def test_the_cli_refuses_an_angle_outside_a_turn(monkeypatch, capsys):
    """Hors d'un tour, il n'y a aucun message proactif à qualifier."""
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")
    monkeypatch.setattr("sys.argv", ["fact-write.py", "angle", "titre Strava"])

    with pytest.raises(InputError) as refus:
        fact_write.main()

    assert "tour" in str(refus.value)


def test_the_angle_is_a_turn_command_so_a_refused_turn_takes_it_along():
    assert commands.PROACTIVE_ANGLE in commands.ACTIVE


def test_both_paths_that_ask_for_an_angle_also_name_where_it_goes():
    """La consigne et sa sortie voyagent ensemble, sinon la sortie n'existe pas.

    `build_proactive_prompt` enveloppe TOUS les déclencheurs : c'est lui qui porte la
    règle générale, et le débrief — le seul qui choisisse explicitement un angle — la
    répète à l'endroit exact où le choix se fait.
    """
    enveloppe = build_proactive_prompt("strava_debrief", "90000000010")
    assert "fact-write.py angle" in enveloppe
    assert "fact-write.py angle" in PROACTIVE_TRIGGERS["strava_debrief"]
