#!/usr/bin/env python3
"""Météo d'un run, pour nourrir le jugement du coach.

Croise le lieu + l'heure de départ d'une activité avec Open-Meteo (gratuit, sans
clé API) et renvoie un résumé compact en français : température, humidité, vent,
précipitations, et le **WBGT** — la charge thermique réelle, qui est ce qui va au
registre. Stdlib uniquement, y compris le WBGT — rien à installer sur le VPS.

Le coach 24/7 récupère le polyline encodé et l'heure de départ avec
`python3 scripts/strava.py activity <id>`, qui les rend prêts à l'emploi dans son
bloc `weather_query`.

⚠ **Strava ne donne qu'une heure murale locale, SANS fuseau.** C'est le seul appel
à connaître :

  python3 scripts/weather.py --polyline 'ki{eFvqfiVsBc@...' \
      --time '2026-08-05T19:01:46' --local

`--local` dit « cette heure est l'heure locale du lieu de départ » et le script
résout le décalage lui-même auprès d'Open-Meteo (`timezone=auto` sur les
coordonnées). **Ne colle JAMAIS un `Z` sur un `start_local` pour le faire
passer** : ça le déclare UTC, donc décalé de 2 h l'été à Paris, donc lu sur la
mauvaise heure de la rampe thermique — c'est exactement le bug du 2026-08-05
(29 °C écrits pour 31-33 °C réels, voir `coaching/corrections.md`).

Sans `--local`, `--time` exige un fuseau explicite (`+02:00`, ou `Z` si et
seulement si l'heure est vraiment en UTC).

Le script lit **deux points** : le départ (1ᵉʳ point du polyline) et le milieu du
parcours. Quand les deux divergent de plus de 2 °C, il le dit — une boucle qui
part des coteaux et descend en ville ne se résume pas par sa météo de départ.

**Le WBGT est ce qui compte, pas la température.** 23 °C à 78 % d'humidité
chargent autant que 31 °C à 37 % ; une colonne de température rangeait ces deux
séances à 8 °C l'une de l'autre. Le calcul est plus bas (portage de Liljegren) et
sa lecture est dans `coaching/protocols.md` § Lire la charge thermique. ⚠ Il se
**consigne**, il ne prescrit rien.

Open-Meteo : l'API `forecast` sert le passé récent, l'API `archive` au-delà. La
bascule et sa raison sont au point de décision, pas ici. Confronté aux METAR de
Paris-Orly et Roissy le 2026-08-06, le modèle colle aux thermomètres réels
à **±0,5 °C** — quand un chiffre paraît faux, c'est l'appel qu'il faut suspecter
(heure, lieu), pas le modèle.
"""

import argparse
import contextlib
import json
import math
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone

from redline.model.errors import ExternalError, InputError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
          "wind_speed_10m,precipitation,weather_code,surface_pressure,"
          "shortwave_radiation,direct_radiation")

# Au-delà de cet écart entre le départ et le milieu du parcours, la météo de
# départ ne décrit plus la séance et le résumé le signale.
ECART_SIGNIFICATIF_C = 2.0

# Codes WMO → libellé FR (sous-ensemble utile pour de la course).
WMO = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grésil",
    80: "averses faibles", 81: "averses", 82: "averses violentes",
    85: "averses de neige", 86: "averses de neige fortes",
    95: "orage", 96: "orage avec grêle", 99: "orage avec forte grêle",
}


# ---------------------------------------------------------------------------
# WBGT extérieur — portage fidèle de Liljegren et al. (2008)
#
# « Modeling the Wet Bulb Globe Temperature Using Standard Meteorological
# Measurements », J Occup Environ Hyg 5(10):645-655. C'est le standard de fait,
# et la seule implémentation dont la littérature de performance (Ely 2007) est
# l'usage direct. Note-source : episodes/src-liljegren-wbgt-model.md
#
# ⚠ **Portage, pas approximation.** Les corrélations, coefficients et constantes
# viennent de la source C publique (mdljts/wbgt, wbgt.c + wbgt.h) et ne se
# « simplifient » pas : un prototype de la session du 2026-08-06 avait bricolé le
# Nusselt de la sphère et sortait un globe faux de plusieurs degrés.
#
# Ce que le modèle demande — et c'est ce qui le rend utilisable ici : T air,
# humidité, pression, vent, rayonnement solaire, fraction directe, cosinus du
# zénith. **Aucun rayonnement infrarouge en entrée** : l'émissivité atmosphérique
# est estimée en interne (`emis_atm`). Open-Meteo sert exactement ces variables.
# ---------------------------------------------------------------------------

STEFANB = 5.6696e-8
CP = 1003.5
M_AIR = 28.97
M_H2O = 18.015
RATIO = CP * M_AIR / M_H2O
R_GAS = 8314.34
R_AIR = R_GAS / M_AIR
PR = CP / (CP + 1.25 * R_AIR)

EMIS_WICK, ALB_WICK, D_WICK, L_WICK = 0.95, 0.4, 0.007, 0.0254
EMIS_GLOBE, ALB_GLOBE, D_GLOBE = 0.95, 0.05, 0.0508
EMIS_SFC, ALB_SFC = 0.999, 0.45

CZA_MIN = 0.00873
MIN_SPEED = 0.13
CONVERGENCE = 0.02
MAX_ITER = 500
SOLAR_CONST = 1367.0
NORMSOLAR_MAX = 0.85

# Drapeaux ACSM des courses sur route. ⚠ Ils sont CONSIGNÉS, jamais appliqués
# comme un plafond : Blackman et al. 2026 (43 études) mesurent ~4 bpm d'écart de
# FC entre deux catégories voisines, moins que ce qu'un changement d'allure ou de
# sommeil produit — une bande situe une journée, elle ne résout pas un individu
# (episodes/src-wbgt-risk-categories-meta.md).
# ⚠ À ne pas confondre avec les catégories de la *politique chaleur extrême* de
# l'ACSM (≤22,2 / 22,3-25,6 / 25,7-27,8 / ≥27,9), un autre découpage — c'est
# celui que teste Blackman. Les drapeaux ci-dessous sont ceux qu'un coureur
# rencontre sur une épreuve, et ils couvrent aussi le froid, ce dont on a besoin
# pour une course de nuit fin novembre.
ACSM_BANDS = ((10.0, "blanc"), (18.0, "vert"), (23.0, "jaune"),
              (28.0, "rouge"), (float("inf"), "noir"))


def acsm_band(wbgt):
    """La bande ACSM du WBGT donné. Voir l'avertissement sur ACSM_BANDS."""
    for limit, name in ACSM_BANDS:
        if wbgt < limit:
            return name
    return "noir"


def esat(tk):
    """Pression de vapeur saturante au-dessus de l'eau liquide, en hPa."""
    y = (tk - 273.15) / (tk - 32.18)
    return 1.004 * 6.1121 * math.exp(17.502 * y)


def dew_point(e):
    """Point de rosée (K) pour une pression de vapeur e (hPa)."""
    z = math.log(e / (6.1121 * 1.004))
    return 240.97 * z / (17.502 - z) + 273.15


def emis_atm(tair, rh):
    """Émissivité atmosphérique estimée depuis T et humidité — c'est elle qui
    dispense de fournir le rayonnement infrarouge descendant."""
    return 0.575 * (rh * esat(tair)) ** 0.143


def viscosity(tair):
    tr = tair / 97.0
    omega = (tr - 2.9) / 0.4 * (-0.034) + 1.048
    return 2.6693e-6 * math.sqrt(M_AIR * tair) / (3.617 ** 2 * omega)


def thermal_cond(tair):
    return (CP + 1.25 * R_AIR) * viscosity(tair)


def diffusivity(tair, pair):
    pcrit13 = (36.4 * 218.0) ** (1.0 / 3.0)
    tcrit512 = (132.0 * 647.3) ** (5.0 / 12.0)
    tcrit12 = math.sqrt(132.0 * 647.3)
    mmix = math.sqrt(1.0 / M_AIR + 1.0 / M_H2O)
    patm = pair / 1013.25
    return 3.640e-4 * (tair / tcrit12) ** 2.334 * pcrit13 * tcrit512 * mmix / patm * 1e-4


def evap(tair):
    return (313.15 - tair) / 30.0 * (-71100.0) + 2.4073e6


def h_sphere_in_air(tair, pair, speed):
    density = pair * 100.0 / (R_AIR * tair)
    re = max(speed, MIN_SPEED) * density * D_GLOBE / viscosity(tair)
    nu = 2.0 + 0.6 * math.sqrt(re) * PR ** 0.3333
    return nu * thermal_cond(tair) / D_GLOBE


def h_cylinder_in_air(tair, pair, speed):
    density = pair * 100.0 / (R_AIR * tair)
    re = max(speed, MIN_SPEED) * density * D_WICK / viscosity(tair)
    nu = 0.281 * re ** 0.6 * PR ** 0.44
    return nu * thermal_cond(tair) / D_WICK


class WBGTError(InputError):
    """Le bilan d'énergie n'a pas convergé — on refuse, on n'invente pas."""


def t_globe(tair, rh, pair, speed, solar, fdir, cza):
    """Température du globe noir (K). Bilan d'énergie itératif."""
    tsfc = tair
    prev = tair
    # Garde anti-division, pas une correction physique : `solar_parameters`
    # renvoie déjà fdir=0 et solar=0 sous CZA_MIN, donc le terme s'annule de
    # toute façon. Sans ce clamp, la nuit produit 0×inf = nan au lieu de 0.
    cza = max(cza, CZA_MIN)
    for _ in range(MAX_ITER):
        tref = 0.5 * (prev + tair)
        h = h_sphere_in_air(tref, pair, speed)
        new = (0.5 * (emis_atm(tair, rh) * tair ** 4 + EMIS_SFC * tsfc ** 4)
               - h / (STEFANB * EMIS_GLOBE) * (prev - tair)
               + solar / (2.0 * STEFANB * EMIS_GLOBE) * (1.0 - ALB_GLOBE)
               * (fdir * (1.0 / (2.0 * cza) - 1.0) + 1.0 + ALB_SFC)) ** 0.25
        if abs(new - prev) < CONVERGENCE:
            return new
        prev = 0.9 * prev + 0.1 * new
    raise WBGTError("température de globe non convergée")


def t_wetbulb(tair, rh, pair, speed, solar, fdir, cza, rad=1):
    """Bulbe humide (K). `rad=1` → NATUREL (ventilé par le vent ambiant, exposé
    au rayonnement) ; `rad=0` → **psychrométrique**, le terme radiatif retiré.

    Le mode `rad=0` n'est pas un ornement : c'est la porte de validation. Le
    bulbe humide psychrométrique est une grandeur qu'Open-Meteo calcule de son
    côté (`wet_bulb_temperature_2m`), par un tout autre chemin. Les faire
    coïncider met à l'épreuve d'un coup `esat`, `diffusivity`, `viscosity`,
    `h_cylinder_in_air`, `evap` et `RATIO` — soit la quasi-totalité de la
    thermodynamique du portage — contre une source indépendante.
    """
    tsfc = tair
    sza = math.acos(max(min(cza, 1.0), -1.0))
    eair = rh * esat(tair)
    prev = dew_point(eair)
    for _ in range(MAX_ITER):
        tref = 0.5 * (prev + tair)
        h = h_cylinder_in_air(tref, pair, speed)
        fatm = (STEFANB * EMIS_WICK
                * (0.5 * (emis_atm(tair, rh) * tair ** 4 + EMIS_SFC * tsfc ** 4) - prev ** 4)
                + (1.0 - ALB_WICK) * solar
                * ((1.0 - fdir) * (1.0 + 0.25 * D_WICK / L_WICK)
                   + fdir * (math.tan(sza) / math.pi + 0.25 * D_WICK / L_WICK)
                   + ALB_SFC))
        ewick = esat(prev)
        density = pair * 100.0 / (R_AIR * tref)
        sc = viscosity(tref) / (density * diffusivity(tref, pair))
        new = (tair - evap(tref) / RATIO * (ewick - eair) / (pair - ewick)
               * (PR / sc) ** 0.56 + fatm / h * rad)
        if abs(new - prev) < CONVERGENCE:
            return new
        prev = 0.9 * prev + 0.1 * new
    raise WBGTError("bulbe humide non convergé")


def sun_position(lat, lon, when):
    """(cos de l'angle zénithal, distance Terre-Soleil en UA) à l'instant donné.
    Position solaire de bas ordre — précision très au-delà de ce qu'une lecture
    horaire d'Open-Meteo justifie de prétendre."""
    utc = when.astimezone(UTC)
    n = (utc - datetime(2000, 1, 1, 12, tzinfo=UTC)).total_seconds() / 86400.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl = math.radians(mean_lon + 1.915 * math.sin(mean_anom)
                       + 0.020 * math.sin(2 * mean_anom))
    obliq = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(obliq) * math.sin(ecl), math.cos(ecl))
    dec = math.asin(math.sin(obliq) * math.sin(ecl))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24.0
    hour_angle = math.radians((gmst * 15.0 + lon) % 360.0) - ra
    phi = math.radians(lat)
    cza = (math.sin(phi) * math.sin(dec)
           + math.cos(phi) * math.cos(dec) * math.cos(hour_angle))
    soldist = 1.00014 - 0.01671 * math.cos(mean_anom) - 0.00014 * math.cos(2 * mean_anom)
    return cza, soldist


def solar_parameters(lat, lon, when, solar):
    """(rayonnement recalé, cos zénith, fraction directe) — `calc_solar_parameters`.

    ⚠ **La fraction directe se DÉRIVE, elle ne se fournit pas.** Open-Meteo sert
    pourtant `direct_radiation`, et l'utiliser paraîtrait plus juste. On ne le
    fait pas : `Tglobe` a été calibrée avec CETTE paramétrisation de `fdir`, et
    y injecter une fraction d'une autre provenance casse l'étalonnage tout en
    rendant le portage invalidable contre la référence. `direct_radiation` reste
    consigné dans la sortie — de quoi tester un jour si l'écart compte, ce qui
    est précisément la forme d'une question falsifiable.

    Le recalage du rayonnement (`solar = normsolar · toasolar`) n'existe que dans
    ce chemin, et c'est lui qui tient le régime « soleil rasant » — celui des
    sorties du soir de l'athlète.
    """
    cza, soldist = sun_position(lat, lon, when)
    toasolar = SOLAR_CONST * max(0.0, cza) / (soldist * soldist)
    if cza < CZA_MIN:
        toasolar = 0.0
    if toasolar <= 0.0:
        return 0.0, cza, 0.0
    normsolar = min(solar / toasolar, NORMSOLAR_MAX)
    solar = normsolar * toasolar
    if normsolar <= 0.0:
        return solar, cza, 0.0
    fdir = math.exp(3.0 - 1.34 * normsolar - 1.65 / normsolar)
    return solar, cza, max(min(fdir, 0.9), 0.0)


def wbgt_outdoor(temp_c, rh_pct, pressure_hpa, wind_kmh, solar, lat, lon, when):
    """WBGT extérieur (°C) = 0,7·Tnwb + 0,2·Tg + 0,1·Ta.

    `wind_kmh` est le vent à 10 m d'Open-Meteo, utilisé tel quel. Liljegren sait
    ramener une vitesse à 2 m, mais son estimateur demande une classe de
    stabilité construite sur un gradient vertical de température qu'on n'a pas ;
    le driver original ne l'applique d'ailleurs que si la hauteur diffère de la
    référence. L'écart joue quelques dixièmes sur le globe.
    """
    tair = temp_c + 273.15
    rh = rh_pct / 100.0
    speed = wind_kmh / 3.6
    solar, cza, fdir = solar_parameters(lat, lon, when, max(solar or 0.0, 0.0))

    tg = t_globe(tair, rh, pressure_hpa, speed, solar, fdir, cza)
    tnwb = t_wetbulb(tair, rh, pressure_hpa, speed, solar, fdir, cza, rad=1)
    wbgt = 0.7 * (tnwb - 273.15) + 0.2 * (tg - 273.15) + 0.1 * temp_c
    return {
        "wbgt": wbgt,
        "tnwb": tnwb - 273.15,
        "tg": tg - 273.15,
        "bande": acsm_band(wbgt),
        "solaire_recale": solar,
        "cos_zenith": cza,
        "fraction_directe": fdir,
    }


def decode_polyline(encoded, precision=5):
    """Décode un polyline encodé Google (celui de Strava) → liste de (lat, lon).
    On n'a besoin que du premier point, mais on décode proprement quand même."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    length = len(encoded)
    while index < length:
        for _ in range(2):
            shift, result = 0, 0
            while True:
                if index >= length:
                    # Polyline tronqué (copie incomplète, échappement de shell
                    # mangé…). Mieux vaut le dire que renvoyer un point faux.
                    raise ValueError(
                        "polyline tronqué ou mal échappé : la dernière "
                        "coordonnée est incomplète. Vérifie la copie "
                        "(les backslashes du JSON doivent rester tels quels)."
                    )
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if _ == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def parse_time(value, local=False):
    """ISO 8601 → datetime. Aware si un fuseau est présent, naive avec `--local`.

    Deux chemins, et un seul est correct pour chaque source :

    - `local=True` : la valeur est une heure **murale au lieu de départ**, sans
      fuseau — c'est exactement ce que rend le champ `start_local` de
      `scripts/strava.py activity`. Le
      décalage est résolu plus tard, depuis les coordonnées (`resolve_offset`),
      parce que lui seul connaît le fuseau du lieu et l'heure d'été à cette date.
    - `local=False` : la valeur DOIT porter un fuseau explicite.

    Le garde-fou précédent (2026-08-05) refusait l'heure sans fuseau sans offrir
    d'alternative — or `start_local` est le seul champ disponible. Il s'est donc
    fait contourner par un `Z` ajouté à la main, qui a produit le chiffre faux
    qu'il devait empêcher. `--local` est le chemin correct qui manquait.
    """
    v = value.strip()
    has_z = v.endswith(("Z", "z"))
    dt = datetime.fromisoformat(v[:-1] + "+00:00" if has_z else v)

    if local:
        if dt.tzinfo is not None:
            raise ValueError(
                f"--local attend une heure SANS fuseau (le `start_local` de "
                f"Strava, ex. '2026-08-05T19:01:46'), reçu {value!r}. Si "
                "l'heure porte déjà son fuseau, retire --local."
            )
        return dt

    if dt.tzinfo is None:
        raise ValueError(
            f"heure sans fuseau : {value!r}. Strava ne rend qu'une heure "
            "murale (`start_local`) : passe-la telle quelle avec "
            "--local, le script résout le décalage depuis les coordonnées. "
            "N'ajoute JAMAIS un `Z` toi-même — ça la déclare UTC et décale la "
            "lecture de 2 h l'été à Paris (bug du 2026-08-05)."
        )
    return dt


def resolve_offset(lat, lon, naive_local):
    """Heure murale au lieu de départ → datetime aware. Renvoie (dt, nom du fuseau).

    Open-Meteo sert le **nom IANA** du fuseau des coordonnées (`timezone=auto`),
    et `zoneinfo` en tire le décalage exact à la date visée — heure d'été
    comprise. C'est ce qui permet au coach de ne jamais déduire un décalage
    lui-même.

    On demande le fuseau SANS plage de dates : les deux API la bornent (le passé
    lointain et le futur lointain sortent de leur fenêtre et renvoient 400),
    alors que le fuseau, lui, ne dépend que des coordonnées. Une course de nuit
    en novembre prochain doit pouvoir être résolue aujourd'hui.
    """
    data = fetch(FORECAST_URL, {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "current": "temperature_2m", "timezone": "auto",
    })
    name = data.get("timezone")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return naive_local.replace(tzinfo=ZoneInfo(name)), name
        except Exception:
            pass  # base tz absente du système : on retombe sur l'offset servi.

    offset = data.get("utc_offset_seconds")
    if offset is None:
        raise RuntimeError(
            "Open-Meteo n'a renvoyé ni nom de fuseau ni décalage pour ces "
            "coordonnées. Repasse --time avec un offset explicite, sans --local."
        )
    # Décalage du jour, pas de la date visée : juste hors saison d'été/hiver.
    return naive_local.replace(tzinfo=timezone(timedelta(seconds=offset))), name


def fetch(url, params):
    query = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 400 and "start_date" in params:
            raise RuntimeError(
                f"Open-Meteo refuse la plage {params['start_date']} → "
                f"{params['end_date']} : hors de la fenêtre servie. Le "
                "prévisionnel ne va qu'à ~16 jours ; une date de course "
                "lointaine ne se lit pas encore."
            ) from exc
        raise


def get_weather(lat, lon, when):
    """Renvoie le dict météo de l'heure la plus proche de `when` (datetime aware).
    Choisit forecast (récent) ou archive (ancien) selon l'âge."""
    now = datetime.now(UTC)
    age_days = (now - when).total_seconds() / 86400
    utc = when.astimezone(UTC)

    # Fenêtre de ±1 jour, pas la seule journée UTC : un départ à 23h locale (la
    # TargetRace part à 23h le 28/11) tombe au bord de la journée UTC, et
    # l'heure la plus proche peut être de l'autre côté de minuit.
    common = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": HOURLY, "timezone": "UTC",
        "start_date": (utc - timedelta(days=1)).strftime("%Y-%m-%d"),
        "end_date": (utc + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    # L'API forecast couvre le passé récent via start_date/end_date (fenêtre de
    # quelques mois) ; au-delà, l'archive prend le relais. `past_days` est
    # incompatible avec une plage de dates, on ne l'utilise donc pas.
    url = FORECAST_URL if age_days <= 60 else ARCHIVE_URL
    data = fetch(url, common)

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError("Open-Meteo n'a renvoyé aucune donnée horaire "
                           "(date trop récente pour l'archive, ou hors couverture ?)")

    target = when.astimezone(UTC).replace(tzinfo=None)
    idx, best = 0, None
    for i, t in enumerate(times):
        diff = abs((datetime.fromisoformat(t) - target).total_seconds())
        if best is None or diff < best:
            best, idx = diff, i

    def at(key):
        seq = hourly.get(key) or []
        return seq[idx] if idx < len(seq) else None

    out = {
        "heure_utc": times[idx],
        "temp": at("temperature_2m"),
        "ressenti": at("apparent_temperature"),
        "humidite": at("relative_humidity_2m"),
        "vent": at("wind_speed_10m"),
        "precip": at("precipitation"),
        "pression": at("surface_pressure"),
        "rayonnement": at("shortwave_radiation"),
        "rayonnement_direct": at("direct_radiation"),
        "conditions": WMO.get(at("weather_code"), "conditions inconnues"),
    }

    # Le WBGT est un CONFORT de lecture, pas la donnée : s'il manque une entrée
    # ou si le bilan d'énergie ne converge pas, on rend la météo sans lui plutôt
    # que de priver le coach de tout. Une clé absente se lit ; un chiffre
    # inventé, non.
    needed = (out["temp"], out["humidite"], out["pression"], out["vent"],
              out["rayonnement"])
    if all(v is not None for v in needed):
        with contextlib.suppress(WBGTError, ValueError, ZeroDivisionError,
                                 OverflowError):
            out.update(wbgt_outdoor(out["temp"], out["humidite"], out["pression"],
                                    out["vent"], out["rayonnement"],
                                    lat, lon, when))
    return out


def format_summary(w, when, milieu=None):
    parts = [f"{w['conditions']}"]
    if w["temp"] is not None:
        s = f"{w['temp']:.0f} °C"
        if w["ressenti"] is not None and abs(w["ressenti"] - w["temp"]) >= 1:
            s += f" (ressenti {w['ressenti']:.0f} °C)"
        parts.append(s)
    if w["humidite"] is not None:
        parts.append(f"humidité {w['humidite']:.0f} %")
    if w["vent"] is not None:
        parts.append(f"vent {w['vent']:.0f} km/h")
    if w["precip"] is not None and w["precip"] > 0:
        parts.append(f"précip {w['precip']:.1f} mm")
    if w.get("wbgt") is not None:
        parts.append(f"**WBGT {w['wbgt']:.0f} °C** ({w['bande']})")
    local = when.strftime("%Y-%m-%d %H:%M %Z")
    out = f"Météo au départ ({local}) : " + ", ".join(parts) + "."

    # Le départ n'est qu'un point. Quand le milieu du parcours en diverge
    # nettement (coteaux → ville, fond de vallée → crête), la météo de départ ne
    # décrit plus la séance et se citerait à tort comme « la météo du run ».
    if milieu and milieu["temp"] is not None and w["temp"] is not None:
        ecart = milieu["temp"] - w["temp"]
        if abs(ecart) >= ECART_SIGNIFICATIF_C:
            out += (
                f" ⚠ Mi-parcours {milieu['temp']:.0f} °C "
                f"({ecart:+.0f} °C) : le départ ne résume pas ce run."
            )
    return out


def main():
    ap = argparse.ArgumentParser(description="Météo d'un run via Open-Meteo.")
    ap.add_argument("--polyline", help="Polyline encodé Strava (summary_polyline).")
    ap.add_argument("--lat", type=float, help="Latitude de départ.")
    ap.add_argument("--lon", type=float, help="Longitude de départ.")
    ap.add_argument("--time", required=True,
                    help="Heure de départ ISO 8601. Avec --local : le "
                         "`start_local` de Strava tel quel, sans fuseau.")
    ap.add_argument("--local", action="store_true",
                    help="--time est l'heure murale au lieu de départ "
                         "(= `start_local` de scripts/strava.py). Le décalage est "
                         "résolu depuis les coordonnées. N'ajoute jamais un `Z` "
                         "à la place.")
    ap.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = ap.parse_args()

    milieu_pt = None
    if args.polyline:
        try:
            pts = decode_polyline(args.polyline)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        if not pts:
            raise InputError("Polyline vide ou illisible.")
        lat, lon = pts[0]
        if len(pts) >= 3:
            milieu_pt = pts[len(pts) // 2]
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        raise InputError("Fournir --polyline, ou --lat et --lon.")

    try:
        when = parse_time(args.time, local=args.local)
    except ValueError as exc:
        raise InputError(str(exc)) from exc

    fuseau = None
    if args.local:
        try:
            when, fuseau = resolve_offset(lat, lon, when)
        except Exception as exc:
            raise ExternalError(f"Fuseau du lieu de départ indéterminable : {exc}") from exc

    try:
        w = get_weather(lat, lon, when)
    except Exception as exc:
        raise ExternalError(f"Météo indisponible : {exc}") from exc

    milieu = None
    if milieu_pt:
        try:
            milieu = get_weather(milieu_pt[0], milieu_pt[1], when)
        except Exception:
            # Le second point est un confort, pas la donnée : son échec ne doit
            # jamais priver le coach de la météo de départ.
            milieu = None

    if args.json:
        payload = {"lat": lat, "lon": lon, "fuseau": fuseau, **w}
        if milieu:
            payload["milieu"] = {"lat": milieu_pt[0], "lon": milieu_pt[1], **milieu}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(format_summary(w, when, milieu))

