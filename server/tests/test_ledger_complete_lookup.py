"""Compléter une mesure ne doit pas exiger un identifiant que rien ne publie.

Le 2026-08-14, Sam donne le RPE et la douleur de son vélo de la veille. Le coach
connaît l'id Strava de la sortie, il tente donc `strava:90000000001`. Mais la ligne
avait été consignée sans id, sous `messenger:2026-08-13:velo-remplace-renfob`, et la
clé n'apparaissait nulle part : ni dans `fact-read.py observations`, ni ailleurs. Elle
n'avait vécu que dans la mémoire de la session — effacée entre-temps.

Résultat : « observation inconnue », tour refusé, incident technique. Encore le même
motif — un contrôle exigeant un chemin correct que rien ne rend accessible.

Deux remèdes ici : la clé se lit (elle est publiée), et la date suffit (c'est ce que
le coach sait vraiment). Plus un troisième, le plus important : l'erreur tombe TOUT DE
SUITE, tant que le coach peut encore relancer, au lieu d'attendre la frontière du tour.
"""

import datetime as dt
import importlib.util

import pytest
from conftest import VAULT_ROOT


def _cli():
    spec = importlib.util.spec_from_file_location(
        "ledger_complete_cli", VAULT_ROOT / "scripts" / "ledger-complete.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def observation_rows(self):
        return self._rows

    def observation_id(self, key):
        return next((row["id"] for row in self._rows
                     if row["idempotency_key"] == key), None)


def _row(date, kind, key, identifier=1):
    return {"id": identifier, "occurred_on": dt.date.fromisoformat(date),
            "kind": kind, "idempotency_key": key}


VELO = _row("2026-08-13", "vélo", "messenger:2026-08-13:velo-remplace-renfob")
REVEIL = _row("2026-08-13", "reveil", "messenger:abc:reveil", identifier=2)


def test_a_date_resolves_the_key_the_coach_cannot_guess():
    """Le cas exact du 2026-08-14 : le coach a la date, pas la clé inventée hier."""
    cli = _cli()
    assert cli.resolve_key(_Store([VELO]), "", "2026-08-13", "") == VELO["idempotency_key"]


def test_an_explicit_key_still_wins():
    cli = _cli()
    assert cli.resolve_key(_Store([VELO]), "clé-explicite", "2026-08-13", "") == "clé-explicite"


def test_an_ambiguous_date_lists_the_candidates_with_their_keys():
    """Deux lignes le même jour : on ne devine pas à la place du coach, on lui montre
    les clés pour qu'il tranche. Une erreur qui n'expose pas la sortie possible oblige
    à deviner une deuxième fois."""
    cli = _cli()
    with pytest.raises(SystemExit) as refus:
        cli.resolve_key(_Store([VELO, REVEIL]), "", "2026-08-13", "")
    message = str(refus.value)
    assert "vélo" in message and "reveil" in message
    assert VELO["idempotency_key"] in message
    assert "--type" in message


def test_a_type_disambiguates_without_needing_the_key():
    cli = _cli()
    assert cli.resolve_key(_Store([VELO, REVEIL]), "", "2026-08-13",
                           "reveil") == REVEIL["idempotency_key"]


def test_a_date_without_any_observation_names_where_to_look():
    cli = _cli()
    with pytest.raises(SystemExit, match=r"fact-read\.py observations"):
        cli.resolve_key(_Store([VELO]), "", "2026-08-01", "")


def test_the_key_is_published_by_the_reader_that_the_coach_runs():
    """Le remède de fond : `ledger-complete.py` exige une clé, donc le lecteur doit la
    publier. Ne pas l'afficher revenait à demander un identifiant secret."""
    import importlib.util as util

    spec = util.spec_from_file_location(
        "fact_read_cli", VAULT_ROOT / "scripts" / "fact-read.py")
    fact_read = util.module_from_spec(spec)
    spec.loader.exec_module(fact_read)

    rows = [{**VELO, "measurements": {"rpe": {"state": "pending"},
                                      "pain": {"state": "pending"}},
             "note": "vélo avec le padre"}]
    lignes = fact_read.observations(_Store(rows), 10)

    assert any(VELO["idempotency_key"] in ligne for ligne in lignes)
    # Et ce qui reste à compléter, sinon le coach ne sait pas quoi réclamer.
    assert any("à compléter : pain, rpe" in ligne for ligne in lignes)
