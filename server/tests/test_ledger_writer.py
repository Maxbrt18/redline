"""L'écrivain typé du registre — les tests de la panne du 2026-08-10.

Ce jour-là : Strava rend `average_heartrate: 156.61`, la colonne `FCmoy` veut
`\\d{2,3}`, et l'arrondi était délégué au jugement du modèle à chaque écriture. Il a
écrit `156.6`. Une cellule a rendu le registre ENTIER illisible — donc `hot-cache.py`,
donc `regen-views.py`, donc (par `set -e` du hook) le commit, donc le débrief Strava
déjà rédigé, jeté sans un mot.

Le registre Markdown n'existe plus : PostgreSQL contraint chaque mesure par colonne, et
une valeur hors type est refusée par la base, pas par une relecture. Ce qui reste de
cette panne, et que ces tests figent, c'est la FRONTIÈRE D'ENTRÉE : `ledger-add.py`
arrondit ce que la source rend en flottant, refuse en nommant la colonne ce qu'aucun
arrondi ne rattrape, et ne retouche jamais une cellule déjà conforme.
"""

import sys

import pytest

from scripts.model import ledger as table

# --------------------------------------------------------------- l'arrondi

@pytest.mark.parametrize("column, source, written", [
    ("FCmoy", "156.61", "157"),      # average_heartrate
    ("Cad", "168.884", "169"),       # average_cadence x2
    ("D+", "64.7", "65"),            # elevation_gain
    ("WBGT", "-3.4", "-3"),          # une nuit d'hiver, et le signe compte
    ("km", "6.8396", "6.84"),
    ("Dlr", "1.55", "1.6"),
    ("FCmoy", "156,61", "157"),      # la virgule décimale vient de la même prose
])
def test_strava_rend_des_flottants_et_la_colonne_les_veut_entiers(column, source, written):
    """LE test de la panne : la source ment sur le type, l'arrondi vit ici."""
    assert table.canonical_value(column, source) == written


def test_une_cellule_deja_conforme_nest_jamais_retouchee():
    """L'invariant qui autorise à passer cette fonction sur toute la ligne.

    « Une mesure connue est immuable » (règle du registre, aujourd'hui tenue par un
    trigger PostgreSQL) commence ici : ce qui est corrigé n'a jamais été une donnée,
    c'était un refus de lecture.
    """
    for item in table.INPUTS:
        for value in ("", table.UNKNOWN):
            assert table.canonical_value(item.name, value) == value
    conforming = {
        "Date": "2026-08-10", "Type": "EF", "km": "6.84", "Temps": "0:41:56",
        "Allure": "6:08", "FCmoy": "157", "FCmax": "168", "Cad": "169",
        "D+": "65", "D-": "64", "WBGT": "20", "RPE": "2", "Dlr": "1.5",
        "Plaisir": "6", "Strava": "90000000003", "Note": "EF du jour",
    }
    for name, value in conforming.items():
        assert table.canonical_value(name, value) == value


def test_ce_qui_nest_pas_un_nombre_nest_pas_inventé():
    """Une plage (« 1-2 ») n'a pas d'arrondi : on ne fabrique pas de la donnée."""
    assert table.canonical_value("RPE", "1-2") == "1-2"
    assert table.canonical_value("Type", "footing") == "footing"


# --------------------------------------------------------------- l'écrivain typé

def test_lecrivain_type_arrondit_ce_que_strava_rend(monkeypatch, capsys):
    """La voie propre : des valeurs BRUTES entrent, une ligne conforme sort."""
    add = _load_writer()
    monkeypatch.setattr(sys, "argv", [
        "ledger-add.py", "--dry-run",
        "--date", "2026-08-10", "--type", "EF",
        "--km", "6.8396", "--temps", "0:41:56", "--allure", "6:08", "--fcmoy", "156.61",
        "--fcmax", "168", "--cad", "168.884", "--dplus", "64.7", "--dmoins", "64",
        "--wbgt", "20", "--strava", "90000000003", "--note", "EF | du jour"])
    assert add.main() == 0
    captured = capsys.readouterr()
    assert "'FCmoy': '157'" in captured.out and "'km': '6.84'" in captured.out
    assert "EF | du jour" in captured.out  # PostgreSQL conserve le texte, pipe compris.


def test_lecrivain_type_refuse_en_nommant_la_colonne(monkeypatch, capsys):
    """Ce qu'aucun arrondi ne rattrape est refusé AVANT toute écriture."""
    add = _load_writer()
    monkeypatch.setattr(sys, "argv", [
        "ledger-add.py", "--date", "2026-08-10", "--type", "footing", "--km", "6.84"])
    assert add.main() == 1
    assert "Type" in capsys.readouterr().err


@pytest.mark.parametrize("trace", ["", "?"])
def test_un_reveil_sans_trace_ne_sécrit_pas(monkeypatch, capsys, trace):
    """Audit du 2026-10-11 : sans `--dlr`, la mesure naissait `pending` sans bruit et la
    réponse de Sam ne vivait que dans la note. La porte ne la lisait jamais."""
    add = _load_writer()
    argv = ["ledger-add.py", "--dry-run", "--date", "2026-10-11", "--type", "reveil",
            "--note", "Trace légère déclarée par Sam"]
    monkeypatch.setattr(sys, "argv", argv + (["--dlr", trace] if trace else []))
    assert add.main() == 1
    assert "réveil sans trace" in capsys.readouterr().err


@pytest.mark.parametrize("word, value", [("aucune", "0"), ("Légère", "1"), ("legere", "1"),
                                         ("très légère", "0.5"), ("nette", "2")])
def test_le_mot_de_alex_devient_la_valeur(monkeypatch, capsys, word, value):
    add = _load_writer()
    monkeypatch.setattr(sys, "argv", [
        "ledger-add.py", "--dry-run", "--date", "2026-10-11", "--type", "reveil",
        "--dlr", word])
    assert add.main() == 0
    assert f"'Dlr': '{value}'" in capsys.readouterr().out


def _load_writer():
    """`ledger-add.py` n'est pas un identifiant Python : il s'importe par son chemin."""
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "ledger-add.py"
    spec = importlib.util.spec_from_file_location("ledger_add", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_observation_is_rejected_before_staging(monkeypatch):
    from unittest.mock import MagicMock

    import scripts.storage

    writer = _load_writer()
    store = MagicMock()
    store.observation_id.return_value = "existing-id"
    monkeypatch.setenv("REDLINE_TURN_ID", "active-turn")
    monkeypatch.setattr(scripts.storage, "Database", MagicMock())
    monkeypatch.setattr(scripts.storage, "OperationalStore", lambda *args: store)
    with pytest.raises(writer.table.LedgerError, match=r"ledger-complete\.py"):
        writer._write_database("test", {"Date": "2026-09-11", "Type": "EF"},
                               "strava:90000000004")
    store.stage_command.assert_not_called()
    store.add_observation.assert_not_called()
