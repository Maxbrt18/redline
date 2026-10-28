"""weather.py : le WBGT extérieur, portage de Liljegren et al. (2008).

Ce que ces tests protègent, c'est un **portage**. Le risque propre à un portage
n'est pas qu'il plante — c'est qu'il se trompe d'un coefficient et sorte quand
même un nombre plausible. Trois familles de garde, aucune redondante :

1. **Validation externe** (`test_bulbe_psychrometrique_*`) : le bulbe humide
   psychrométrique est le même code avec le terme radiatif à zéro, et c'est une
   grandeur qu'Open-Meteo calcule par un tout autre chemin. Les valeurs de
   référence sont **gelées** ici (relevées le 2026-08-06, 6 climats × 2 heures) :
   un test ne va pas sur le réseau. C'est le seul test qui puisse attraper une
   constante thermodynamique fausse.
2. **Invariants physiques** : ce qui doit rester vrai quelles que soient les
   valeurs — la nuit refroidit le globe, l'humidité augmente le WBGT, le bulbe
   humide reste sous l'air.
3. **Vecteurs figés** : la régression pure, sur les séances réelles de Sam.

⚠ Ce qui manque, et qu'il faut savoir : le code C de référence n'a pas pu être
exécuté ici (`pywbgt` exige Python ≥ 3.10, la machine est en 3.9). L'équivalence
au C repose sur un audit ligne à ligne, pas sur une exécution comparée.
"""

from datetime import UTC, datetime

import pytest

from redline.integrations import weather


def _noon():
    """Un midi solaire d'été à Paris — soleil haut, `cza` large."""
    return datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _midnight():
    return datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


# Relevé le 2026-08-06 sur api.open-meteo.com (`wet_bulb_temperature_2m`), pour
# 6 climats contrastés. (lat, lon, Ta °C, HR %, pression hPa, vent km/h, Twb attendu)
PSYCHRO_REFERENCE = [
    ("Paris 03h", 24.0, 76, 998.4, 2.6, 20.80),
    ("Paris 15h", 33.7, 31, 998.3, 9.3, 21.40),
    ("Ville-A 03h", 16.2, 78, 961.4, 2.6, 13.60),
    ("Ville-A 15h", 28.5, 31, 960.8, 4.2, 17.40),
    ("Dubai 03h", 33.1, 61, 996.7, 7.4, 26.90),
    ("Dubai 15h", 36.0, 61, 995.5, 8.7, 29.50),
    ("Singapour 03h", 29.8, 76, 1005.6, 7.9, 26.30),
    ("Singapour 15h", 26.7, 84, 1005.6, 5.1, 24.50),
    ("Reykjavik 03h", 9.3, 64, 1018.2, 15.5, 5.80),
    ("Reykjavik 15h", 15.4, 57, 1018.6, 16.9, 10.60),
    ("Sydney 03h", 15.3, 77, 1023.9, 0.0, 12.60),
    ("Sydney 15h", 10.1, 92, 1023.7, 6.1, 9.20),
]


def psychrometric(ta_c, rh_pct, pressure, wind_kmh):
    """Bulbe humide psychrométrique en °C — le mode `rad=0` du portage."""
    return weather.t_wetbulb(ta_c + 273.15, rh_pct / 100.0, pressure,
                             wind_kmh / 3.6, 0.0, 0.0, 0.5, rad=0) - 273.15


@pytest.mark.parametrize("label,ta,rh,pressure,wind,expected", PSYCHRO_REFERENCE)
def test_bulbe_psychrometrique_colle_a_une_source_independante(
        label, ta, rh, pressure, wind, expected):
    """±1 °C d'une implémentation qui ne partage aucune ligne avec la nôtre.

    La tolérance n'est pas de la complaisance : les écarts les plus gros tombent
    tous en chaud-et-sec (33,7 °C à 31 %), le régime où la formule empirique de
    Stull qu'emploie Open-Meteo est elle-même la moins bonne. Un désaccord de
    2 °C, lui, voudrait dire qu'une constante a sauté.
    """
    assert psychrometric(ta, rh, pressure, wind) == pytest.approx(expected, abs=1.0)


def test_bulbe_psychrometrique_sans_biais_systematique():
    """Un biais moyen serait le symptôme d'un coefficient faux — un écart
    dispersé, celui de deux approximations différentes. C'est la distinction que
    douze points isolés ne font pas et que leur moyenne fait."""
    errors = [psychrometric(ta, rh, p, w) - expected
              for _, ta, rh, p, w, expected in PSYCHRO_REFERENCE]
    assert abs(sum(errors) / len(errors)) < 0.3


def test_la_nuit_le_globe_refroidit_sous_l_air():
    """Sans soleil, le globe rayonne vers un ciel plus froid que l'air : Tg < Ta.
    Le signe est le test — c'est lui qui distingue un bilan d'énergie d'une
    formule empirique recopiée à l'envers."""
    tg = weather.t_globe(301.5, 0.43, 977.0, 3.6, solar=0.0, fdir=0.0, cza=0.0)
    assert tg < 301.5
    assert 301.5 - tg < 3.0


def test_en_plein_soleil_le_globe_depasse_largement_l_air():
    tg = weather.t_globe(306.5, 0.31, 975.0, 2.6, solar=770.0, fdir=0.85, cza=0.77)
    assert tg - 306.5 > 8.0


def test_le_wbgt_croit_avec_l_humidite_a_air_constant():
    """C'est toute la raison d'être de l'indice : 23 °C à 78 % charge plus que
    31 °C à 37 %. Si la monotonie casse, l'instrument dit le contraire de ce
    pour quoi il a été ajouté."""
    values = [
        weather.wbgt_outdoor(28.0, rh, 1013.0, 10.0, 300.0, 45.76, 4.84,
                             _noon())["wbgt"]
        for rh in (20, 40, 60, 80, 95)
    ]
    assert values == sorted(values)


def test_le_bulbe_humide_naturel_reste_sous_l_air_en_air_non_sature():
    tair = 306.5
    tnwb = weather.t_wetbulb(tair, 0.31, 975.0, 2.6, 770.0, 0.85, 0.77, rad=1)
    assert tnwb < tair


def test_la_nuit_le_soleil_ne_contribue_pas():
    """`solar_parameters` doit annuler et le rayonnement et la fraction directe
    sous l'horizon — sinon `1/(2·cza)` explose dans le bilan du globe."""
    solar, cza, fdir = weather.solar_parameters(45.76, 4.84, _midnight(), 0.0)
    assert cza < weather.CZA_MIN
    assert solar == 0.0
    assert fdir == 0.0


def test_le_rayonnement_est_recale_sous_le_plafond_physique():
    """Un rayonnement mesuré aberrant (ici 5000 W/m², impossible) doit être
    ramené sous `NORMSOLAR_MAX × toa`, pas propagé tel quel dans le globe."""
    solar, cza, _ = weather.solar_parameters(45.76, 4.84, _noon(), 5000.0)
    toa = weather.SOLAR_CONST * cza
    assert solar <= toa * weather.NORMSOLAR_MAX * 1.02


def test_la_fraction_directe_reste_bornee():
    for measured in (0.0, 50.0, 400.0, 900.0, 5000.0):
        _, _, fdir = weather.solar_parameters(45.76, 4.84, _noon(), measured)
        assert 0.0 <= fdir <= 0.9


def test_les_bandes_acsm_aux_bornes():
    assert weather.acsm_band(9.9) == "blanc"
    assert weather.acsm_band(17.9) == "vert"
    assert weather.acsm_band(22.9) == "jaune"
    assert weather.acsm_band(27.9) == "rouge"
    assert weather.acsm_band(28.0) == "noir"


def test_le_non_convergent_leve_au_lieu_de_rendre_un_nombre(monkeypatch):
    """Liljegren rend -9999 ; ici on lève. Une sentinelle numérique finit
    toujours par être écrite au registre comme une température — `ledger.py`
    porte déjà tout un commentaire sur une cellule qui avait pris deux sens
    opposés (2026-08-05)."""
    monkeypatch.setattr(weather, "MAX_ITER", 1)
    with pytest.raises(weather.WBGTError):
        weather.t_globe(300.0, 0.5, 1013.0, 2.0, 500.0, 0.5, 0.5)
    with pytest.raises(weather.WBGTError):
        weather.t_wetbulb(300.0, 0.5, 1013.0, 2.0, 500.0, 0.5, 0.5)


def test_une_seance_reelle_reste_lisible_de_bout_en_bout():
    """Vecteur figé sur le 05/08 — la séance qui a déclenché tout ce chantier.
    Elle relie les entrées brutes au verdict lu par le coach."""
    out = weather.wbgt_outdoor(
        temp_c=31.0, rh_pct=37, pressure_hpa=974.0, wind_kmh=18.6,
        solar=305.0, lat=45.72627, lon=4.70394,
        when=datetime(2026, 8, 5, 17, 0, tzinfo=UTC))
    assert out["wbgt"] == pytest.approx(25.3, abs=0.6)
    assert out["bande"] == "rouge"
    # Le globe très au-dessus de l'air : soleil rasant mais franc.
    assert out["tg"] - 31.0 > 5.0
