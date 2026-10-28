"""model/hot_cache.py : le bloc Forme du hot-cache, généré depuis le registre.

Ce que ces tests protègent : que le hot-cache ne puisse plus porter un chiffre FAUX
en silence. Avant, ces valeurs étaient tapées à la main — une ligne ajoutée au
registre les périmait jusqu'à ce que quelqu'un relance le script ET pense à rééditer
le cache. C'est le mode de panne qui a tué `coaching/fitness-status.md`, figé au
12/07 pendant que le hot-cache disait autre chose.

Ce que ces tests ne protègent PAS, et volontairement : que le bloc VERSIONNÉ soit à
jour. C'est du contenu de vault, pas du code — ça vit dans `vault-check.py`
(`check_generated_views`), comme le dit déjà l'en-tête de `code-checks.yml`. Le test
a existé ici du 25 au 29/07 et il a rougi la CI deux fois de suite pour une ligne
ajoutée au registre : un gate qui crie au loup sur une note qui bouge finit ignoré.
La dérive est désormais rendue impossible en amont — `redline/vault.py` régénère
avant de committer, `.githooks/pre-commit` couvre les clones humains.
"""

import datetime as dt

from conftest import RPE_TARGETS

from scripts import ledger
from scripts.ledger import Instruments, Row
from scripts.model import hot_cache


def test_the_block_carries_the_gauge_of_the_last_week():
    """Chiffres synthétiques, pas ceux du vrai registre : sinon ce test casse à
    chaque semaine qui passe (W30 devient W31...), pour une raison qui n'a rien
    à voir avec un bug de `render()`. C'est exactement le mode de panne qui a fait
    échouer la CI le 2026-07-29."""
    rows = [Row(dt.date(2026, 3, 2), "easy", km=10, hours=5.0),
            Row(dt.date(2026, 3, 4), "easy", km=10, hours=5.0)]
    block = hot_cache.render(ledger, rows, {}, RPE_TARGETS)
    assert "W10" in block
    assert "10 h-éq · 140/100" in block   # h_eq = 5+5 ; level = round(10 * 14)
    assert "au rouge" in block           # 140 > LEVEL_CAP (85)


def test_a_red_made_of_walking_says_so():
    """Une jauge rouge faite de rando ne se lit pas comme une jauge rouge faite de
    course (objection Sam du 21/07, retenue). Le bloc doit porter la part de
    marche, sinon la lecture se refait de tête chaque semaine — et se perd."""
    rows = [Row(dt.date(2026, 3, 2), "easy", km=10, hours=2.0),
            Row(dt.date(2026, 3, 4), "hike", km=15, hours=6.0, dplus=300)]
    assert "% de marche" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)


def test_the_walk_share_uses_the_gauge_formula_not_a_copy_of_it():
    """NON-RÉGRESSION (2026-07-29) : `hot_cache` portait sa propre copie de la
    pondération et cette copie avait perdu le +0,5 de la séance de qualité RATÉE.
    Même ligne du bloc, deux arithmétiques : le h-éq d'un côté, la part de marche de
    l'autre — qui s'en trouvait gonflée et pouvait annoncer un rouge de blessure que la
    jauge dément.

    Ici : seuil 1 h à RPE 8 (cible 6,5) = 2,0 h-éq · rando 2 h = 1,0 h-éq, donc 33 %
    de marche. Avec la copie fautive le seuil pesait 1,5 et la marche affichait 40 %.
    """
    rows = [Row(dt.date(2026, 3, 2), "threshold", km=10, hours=1.0, rpe=8.0),
            Row(dt.date(2026, 3, 4), "hike", km=8, hours=2.0)]
    assert ledger.row_weight(rows[0], RPE_TARGETS) == (2.0, True)
    assert "33 % de marche" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)


def test_a_quiet_week_carries_no_red_verdict():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8, pains={"injury": 0.0}),
            Row(dt.date(2026, 3, 4), "easy", km=8.0, hours=0.8, pains={"injury": 0.0})]
    block = hot_cache.render(ledger, rows, {"2026-W10": Instruments()}, RPE_TARGETS)
    assert "au rouge" not in block
    assert "% de marche" not in block   # zéro rando : la mention n'a pas lieu d'être
    assert "2 sorties" in block


def test_an_empty_ledger_does_not_crash():
    assert "vide" in hot_cache.render(ledger, [], {}, RPE_TARGETS)


# ------------------------------------------- semaine ouverte et bloc État (2026-09-27)

def test_the_week_in_progress_is_never_announced_as_closed():
    """Le samedi 26/09, le bloc annonçait « W39 bouclée — 13 km, ratio 0,3 »."""
    rows = [Row(dt.date(2026, 9, 22), "easy", km=7.0, hours=0.75),
            Row(dt.date(2026, 9, 24), "easy", km=6.0, hours=0.65)]
    saturday = hot_cache.render(ledger, rows, {}, RPE_TARGETS, today=dt.date(2026, 9, 26))
    assert "W39 en cours" in saturday and "bouclée" not in saturday
    monday = hot_cache.render(ledger, rows, {}, RPE_TARGETS, today=dt.date(2026, 9, 28))
    assert "W39 bouclée" in monday


def _state(**overrides):
    rows = []
    for day, km in (("2026-09-06", 10.4), ("2026-09-13", 11.53),
                    ("2026-09-20", 10.8), ("2026-09-27", 11.0)):
        rows.append(Row(dt.date.fromisoformat(day), "long", km=km, hours=km / 8))
    inputs = {
        "deadlines": [
            {"deadline_key": "verdict-cortex", "due_on": dt.date(2026, 9, 6),
             "status": "todo", "blocks": "descente courue"},
            {"deadline_key": "targetrace", "due_on": dt.date(2026, 11, 28),
             "status": "scheduled", "kind": "race"},
        ],
        "capabilities": [{"slug": "durabilite", "score": 2, "scale": 5},
                         {"slug": "descente", "score": 1, "scale": 5}],
        "typed_due": 0,
        "race_on": dt.date(2026, 11, 28), "today": dt.date(2026, 10, 5),
    }
    inputs.update(overrides)
    return hot_cache.render_state(ledger, rows, **inputs)


def test_the_state_block_carries_what_decides_the_next_weeks():
    block = _state()
    assert "sans nouveau record depuis 3 semaine(s) close(s)" in block
    assert "protocole des baisses ouvert" in block
    assert "course le 28/11, dans 7 semaine(s)" in block
    assert "verdict-cortex (06/09), bloque descente courue" in block
    assert "targetrace" not in block.split("Échéances échues")[1].split("\n")[0]
    assert "durabilite 2/5 · descente 1/5" in block


def test_the_state_block_does_not_change_from_one_day_to_the_next():
    """Contrôlé contre sa régénération : une date du jour le ferait dériver chaque
    matin. Mardi et mercredi de la même semaine, sans fait nouveau, même rendu."""
    assert _state(today=dt.date(2026, 10, 6)) == _state(today=dt.date(2026, 10, 7))


def test_refresh_fills_the_state_block_only_where_it_is_declared(tmp_path):
    (tmp_path / "ai").mkdir()
    cache = tmp_path / "ai" / "hot-cache.md"
    cache.write_text("<!-- FORME_START -->\n<!-- FORME_END -->\n"
                     "<!-- ETAT_START -->\n<!-- ETAT_END -->\n", encoding="utf-8")
    (tmp_path / "coaching" / "workouts").mkdir(parents=True)

    class Store:
        def deadline_rows(self):
            return []

        def latest_capabilities(self):
            return [{"slug": "injury", "score": 3, "scale": 5}]

        def prediction_rows(self):
            return [{"status": "open", "due_on": dt.date(2026, 9, 1)}]

        def pain_zones(self):
            return [{"slug": "injury", "label": "Injury", "active": True}]

    rows = [Row(dt.date(2026, 9, 20), "long", km=10.8, hours=1.3)]
    assert hot_cache.refresh(tmp_path, ledger, rows, {}, store=Store(),
                             today=dt.date(2026, 9, 27))
    text = cache.read_text(encoding="utf-8")
    assert "injury 3/5" in text and "Prédictions à solder** : 1" in text
    assert not hot_cache.refresh(tmp_path, ledger, rows, {}, store=Store(),
                                 today=dt.date(2026, 9, 27), check=True)


def test_each_watched_zone_has_its_own_trend_in_the_form_block():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8,
                pains={"genou": 2.0, "cheville": 0.5})]
    zones = [ledger.PainZone("genou", "Genou"), ledger.PainZone("cheville", "Cheville")]
    block = hot_cache.render(ledger, rows, {}, RPE_TARGETS, zones=zones)
    assert "Genou W10 2/—" in block and "Cheville W10 0,5/—" in block


def test_without_a_watched_zone_the_form_block_says_so():
    rows = [Row(dt.date(2026, 3, 2), "easy", km=8.0, hours=0.8)]
    assert "aucune zone surveillée" in hot_cache.render(ledger, rows, {}, RPE_TARGETS)
