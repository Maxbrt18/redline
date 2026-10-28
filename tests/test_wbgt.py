"""weather: the outdoor WBGT, a port of Liljegren et al. (2008).

These tests protect a **port**. The risk specific to a port is not that it crashes -- it is
that it gets a coefficient wrong and still outputs a plausible number. Three families of
guard, none redundant:

1. **External validation** (`test_bulbe_psychrometrique_*`): the psychrometric wet bulb is
   the same code with the radiative term at zero, and it is a quantity Open-Meteo computes
   through a completely different path. The reference values are **frozen** here
   (6 climates x 2 hours): a test does not go on the network. This is the only test that
   can catch a wrong thermodynamic constant.
2. **Physical invariants**: what must stay true whatever the values -- night cools the
   globe, humidity raises the WBGT, the wet bulb stays below the air temperature.
3. **Frozen vectors**: pure regression, on real sessions.

Known gap: the reference C code could not be run here (`pywbgt` requires a newer Python
than the test machine). Equivalence with the C rests on a line-by-line audit, not on a
side-by-side execution.
"""

from datetime import UTC, datetime

import pytest

from redline.integrations import weather


def _noon():
    """A summer solar noon in Paris -- high sun, large `cza`."""
    return datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _midnight():
    return datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


# Frozen from api.open-meteo.com (`wet_bulb_temperature_2m`) for 6 contrasting climates.
# Columns: label, Ta degC, RH %, pressure hPa, wind km/h, expected Twb.
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
    """Psychrometric wet bulb in degC -- the `rad=0` mode of the port."""
    return weather.t_wetbulb(ta_c + 273.15, rh_pct / 100.0, pressure,
                             wind_kmh / 3.6, 0.0, 0.0, 0.5, rad=0) - 273.15


@pytest.mark.parametrize("label,ta,rh,pressure,wind,expected", PSYCHRO_REFERENCE)
def test_bulbe_psychrometrique_colle_a_une_source_independante(
        label, ta, rh, pressure, wind, expected):
    """Within 1 degC of an implementation that shares no line with ours.

    The tolerance is not complacency: the largest gaps all fall in hot-and-dry (33.7 degC
    at 31 %), the regime where Stull's empirical formula, used by Open-Meteo, is itself
    least accurate. A 2 degC disagreement, on the other hand, would mean a constant broke.
    """
    assert psychrometric(ta, rh, pressure, wind) == pytest.approx(expected, abs=1.0)


def test_bulbe_psychrometrique_sans_biais_systematique():
    """A mean bias would be the symptom of a wrong coefficient -- a scattered gap is that
    of two different approximations. Twelve isolated points do not show the difference;
    their mean does."""
    errors = [psychrometric(ta, rh, p, w) - expected
              for _, ta, rh, p, w, expected in PSYCHRO_REFERENCE]
    assert abs(sum(errors) / len(errors)) < 0.3


def test_la_nuit_le_globe_refroidit_sous_l_air():
    """Without sun, the globe radiates toward a sky colder than the air: Tg < Ta.
    The sign is the test -- it tells an energy balance from an empirical formula copied
    upside down."""
    tg = weather.t_globe(301.5, 0.43, 977.0, 3.6, solar=0.0, fdir=0.0, cza=0.0)
    assert tg < 301.5
    assert 301.5 - tg < 3.0


def test_en_plein_soleil_le_globe_depasse_largement_l_air():
    tg = weather.t_globe(306.5, 0.31, 975.0, 2.6, solar=770.0, fdir=0.85, cza=0.77)
    assert tg - 306.5 > 8.0


def test_le_wbgt_croit_avec_l_humidite_a_air_constant():
    """This is the whole point of the index: 23 degC at 78 % loads more than
    31 degC at 37 %. If monotonicity breaks, the instrument says the opposite of what it
    was added for."""
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
    """`solar_parameters` must zero both the radiation and the direct fraction
    below the horizon -- otherwise `1/(2*cza)` blows up in the globe balance."""
    solar, cza, fdir = weather.solar_parameters(45.76, 4.84, _midnight(), 0.0)
    assert cza < weather.CZA_MIN
    assert solar == 0.0
    assert fdir == 0.0


def test_le_rayonnement_est_recale_sous_le_plafond_physique():
    """An absurd measured radiation (here 5000 W/m2, impossible) must be brought under
    `NORMSOLAR_MAX x toa`, not propagated as is into the globe."""
    solar, cza, _ = weather.solar_parameters(45.76, 4.84, _noon(), 5000.0)
    toa = weather.SOLAR_CONST * cza
    assert solar <= toa * weather.NORMSOLAR_MAX * 1.02


def test_la_fraction_directe_reste_bornee():
    for measured in (0.0, 50.0, 400.0, 900.0, 5000.0):
        _, _, fdir = weather.solar_parameters(45.76, 4.84, _noon(), measured)
        assert 0.0 <= fdir <= 0.9


def test_les_bandes_acsm_aux_bornes():
    assert weather.acsm_band(9.9) == "white"
    assert weather.acsm_band(17.9) == "green"
    assert weather.acsm_band(22.9) == "yellow"
    assert weather.acsm_band(27.9) == "red"
    assert weather.acsm_band(28.0) == "black"


def test_le_non_convergent_leve_au_lieu_de_rendre_un_nombre(monkeypatch):
    """Liljegren returns -9999; here we raise. A numeric sentinel always ends up written
    to the ledger as a temperature."""
    monkeypatch.setattr(weather, "MAX_ITER", 1)
    with pytest.raises(weather.WBGTError):
        weather.t_globe(300.0, 0.5, 1013.0, 2.0, 500.0, 0.5, 0.5)
    with pytest.raises(weather.WBGTError):
        weather.t_wetbulb(300.0, 0.5, 1013.0, 2.0, 500.0, 0.5, 0.5)


def test_une_seance_reelle_reste_lisible_de_bout_en_bout():
    """Frozen vector on a real session. It links the raw inputs to the verdict the coach
    reads."""
    out = weather.wbgt_outdoor(
        temp_c=31.0, rh_pct=37, pressure_hpa=974.0, wind_kmh=18.6,
        solar=305.0, lat=45.72627, lon=4.70394,
        when=datetime(2026, 8, 5, 17, 0, tzinfo=UTC))
    assert out["wbgt"] == pytest.approx(25.3, abs=0.6)
    assert out["band"] == "red"
    # The globe is far above the air: low but strong sun.
    assert out["tg"] - 31.0 > 5.0
