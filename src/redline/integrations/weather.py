#!/usr/bin/env python3
"""Weather of a run, to inform the coach's judgement.

Combines the place and start time of an activity with Open-Meteo (free, no API key)
and returns a compact French summary: temperature, humidity, wind, precipitation,
and the **WBGT**, the real heat load, which is what goes into the ledger. Standard
library only, WBGT included, so nothing has to be installed on the server.

The coach gets the encoded polyline and the start time from
`redline strava activity <id>`, which returns them ready to use in its
`weather_query` block.

Strava only gives a local wall-clock time, WITHOUT a timezone. This is the one call
to know:

  redline weather --polyline 'ki{eFvqfiVsBc@...' \
      --time '2030-06-15T19:01:46' --local

`--local` means "this time is the local time of the start place"; the script resolves
the offset itself from Open-Meteo (`timezone=auto` on the coordinates). NEVER append
a `Z` to a `start_local` to make it pass: that declares it UTC, so it is shifted by
the local offset and read at the wrong hour of the thermal ramp.

Without `--local`, `--time` requires an explicit timezone (`+02:00`, or `Z` only if
the time really is UTC).

The script reads **two points**: the start (first polyline point) and the middle of
the route. When the two differ by more than 2 degrees C it says so: a loop that
starts on hillsides and descends into town is not summed up by its start weather.

**WBGT is what matters, not air temperature.** 23 C at 78 % humidity loads as much as
31 C at 37 %; a temperature column would rank those two sessions 8 C apart. The
computation is below (Liljegren port) and how to read it is in `coaching/protocols.md`.
It is recorded, it prescribes nothing.

Open-Meteo: the `forecast` API serves the recent past, the `archive` API anything
older. The switch and its reason are at the decision point, not here. Checked against
airport METAR observations, the model matches real thermometers to about +-0.5 C:
when a figure looks wrong, suspect the call (time, place), not the model.
"""

import argparse
import contextlib
import json
import math
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone

from redline.coach.fr.weather import FLAGS, sky
from redline.model.errors import ExternalError, InputError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ("temperature_2m,apparent_temperature,relative_humidity_2m,"
          "wind_speed_10m,precipitation,weather_code,surface_pressure,"
          "shortwave_radiation,direct_radiation")

# Beyond this gap between the start and the middle of the route, the start weather no
# longer describes the session and the summary flags it.
MIDPOINT_GAP_C = 2.0


# ---------------------------------------------------------------------------
# Outdoor WBGT: faithful port of Liljegren et al. (2008)
#
# "Modeling the Wet Bulb Globe Temperature Using Standard Meteorological
# Measurements", J Occup Environ Hyg 5(10):645-655. It is the de facto standard.
#
# This is a port, not an approximation. The correlations, coefficients and constants
# come from the public C source (mdljts/wbgt, wbgt.c + wbgt.h) and must not be
# "simplified": an altered sphere Nusselt number yields a globe temperature that is
# wrong by several degrees.
#
# Inputs the model needs: air temperature, humidity, pressure, wind, solar radiation,
# direct fraction, cosine of the zenith angle. No infrared radiation input: the
# atmospheric emissivity is estimated internally (`emis_atm`). Open-Meteo serves
# exactly these variables.
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

# ACSM flags for road races. They are RECORDED, never applied as a ceiling: a meta-analysis
# of 43 studies measures about 4 bpm of heart-rate difference between adjacent
# categories, less than a change of pace or sleep produces. A band places a day, it
# does not resolve an individual.
# Not to be confused with the ACSM extreme-heat policy categories
# (<=22.2 / 22.3-25.6 / 25.7-27.8 / >=27.9), a different split. The flags below are
# the ones a runner meets at a race, and they also cover cold.
ACSM_BANDS = ((10.0, "white"), (18.0, "green"), (23.0, "yellow"),
              (28.0, "red"), (float("inf"), "black"))


def acsm_band(wbgt):
    """The ACSM band of the given WBGT. See the warning on ACSM_BANDS."""
    for limit, name in ACSM_BANDS:
        if wbgt < limit:
            return name
    return "black"


def esat(tk):
    """Saturation vapour pressure over liquid water, in hPa."""
    y = (tk - 273.15) / (tk - 32.18)
    return 1.004 * 6.1121 * math.exp(17.502 * y)


def dew_point(e):
    """Dew point (K) for a vapour pressure e (hPa)."""
    z = math.log(e / (6.1121 * 1.004))
    return 240.97 * z / (17.502 - z) + 273.15


def emis_atm(tair, rh):
    """Atmospheric emissivity estimated from T and humidity; it removes the need
    to supply downwelling infrared radiation."""
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
    """The energy balance did not converge: refuse rather than invent a value."""


def t_globe(tair, rh, pair, speed, solar, fdir, cza):
    """Black globe temperature (K). Iterative energy balance."""
    tsfc = tair
    prev = tair
    # Division guard, not a physical correction: `solar_parameters` already returns
    # fdir=0 and solar=0 below CZA_MIN, so the term cancels anyway. Without this
    # clamp, night produces 0*inf = nan instead of 0.
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
    """Wet bulb (K). `rad=1` is the NATURAL wet bulb (ventilated by ambient wind,
    exposed to radiation); `rad=0` is the **psychrometric** one, radiative term removed.

    The `rad=0` mode is the validation hook: the psychrometric wet bulb is a quantity
    Open-Meteo computes on its side (`wet_bulb_temperature_2m`) by a different path.
    Making them agree tests `esat`, `diffusivity`, `viscosity`, `h_cylinder_in_air`,
    `evap` and `RATIO` at once, nearly all the thermodynamics of the port, against an
    independent source.
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
    """(cosine of the zenith angle, Earth-Sun distance in AU) at the given instant.
    Low-order solar position, far more precise than an hourly Open-Meteo reading
    justifies."""
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
    """(rescaled radiation, cosine of zenith, direct fraction), `calc_solar_parameters`.

    The direct fraction is DERIVED, not supplied. Open-Meteo serves `direct_radiation`
    and using it might look more accurate, but `Tglobe` was calibrated with THIS
    parametrisation of `fdir`; injecting a fraction of another origin breaks the
    calibration and makes the port impossible to validate against the reference.
    `direct_radiation` stays recorded in the output so the difference can be tested.

    The radiation rescaling (`solar = normsolar * toasolar`) exists only in this path,
    and it handles the low-sun regime (evening sessions).
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
    """Outdoor WBGT (C) = 0.7*Tnwb + 0.2*Tg + 0.1*Ta.

    `wind_kmh` is the Open-Meteo 10 m wind, used as is. Liljegren can scale a speed to
    2 m, but its estimator needs a stability class built on a vertical temperature
    gradient that is not available; the original driver only applies it when the
    height differs from the reference. The difference is a few tenths on the globe.
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
        "band": acsm_band(wbgt),
        "solar_adjusted": solar,
        "cos_zenith": cza,
        "direct_fraction": fdir,
    }


def decode_polyline(encoded, precision=5):
    """Decode a Google encoded polyline (Strava's) into a list of (lat, lon).
    Only the first point is needed, but the whole line is decoded properly."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    length = len(encoded)
    while index < length:
        for _ in range(2):
            shift, result = 0, 0
            while True:
                if index >= length:
                    # Truncated polyline (incomplete copy, eaten shell escape...).
                    # Better to say so than to return a wrong point.
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
    """ISO 8601 to datetime. Aware if a timezone is present, naive with `--local`.

    Two paths, and only one is correct for each source:

    - `local=True`: the value is a **wall-clock time at the start place**, without a
      timezone, which is what the `start_local` field of `redline strava activity`
      returns. The offset is resolved later from the coordinates (`resolve_offset`),
      because only that step knows the place's timezone and daylight saving at that
      date.
    - `local=False`: the value MUST carry an explicit timezone.

    Rejecting a time without a timezone with no alternative would push callers to
    append a `Z` by hand, producing the wrong figure the guard exists to prevent;
    `--local` is the correct path.
    """
    v = value.strip()
    has_z = v.endswith(("Z", "z"))
    dt = datetime.fromisoformat(v[:-1] + "+00:00" if has_z else v)

    if local:
        if dt.tzinfo is not None:
            raise ValueError(
                f"--local attend une heure SANS fuseau (le `start_local` de "
                f"Strava, ex. '2030-06-15T19:01:46'), reçu {value!r}. Si "
                "l'heure porte déjà son fuseau, retire --local."
            )
        return dt

    if dt.tzinfo is None:
        raise ValueError(
            f"heure sans fuseau : {value!r}. Strava ne rend qu'une heure "
            "murale (`start_local`) : passe-la telle quelle avec "
            "--local, le script résout le décalage depuis les coordonnées. "
            "N'ajoute JAMAIS un `Z` toi-même — ça la déclare UTC et décale la "
            "lecture de l'écart entre UTC et l'heure locale."
        )
    return dt


def resolve_offset(lat, lon, naive_local):
    """Wall-clock time at the start place to aware datetime. Returns (dt, timezone name).

    Open-Meteo serves the **IANA name** of the timezone of the coordinates
    (`timezone=auto`), and `zoneinfo` derives the exact offset at the target date,
    daylight saving included. The caller never has to infer an offset itself.

    The timezone is requested WITHOUT a date range: both APIs bound it (the far past
    and far future fall outside their window and return 400), while the timezone
    depends only on the coordinates. A future date must be resolvable today.
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
            pass  # tz database missing on the system: fall back to the served offset.

    offset = data.get("utc_offset_seconds")
    if offset is None:
        raise RuntimeError(
            "Open-Meteo n'a renvoyé ni nom de fuseau ni décalage pour ces "
            "coordonnées. Repasse --time avec un offset explicite, sans --local."
        )
    # Offset of today, not of the target date: wrong across a daylight-saving change.
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
    """Return the weather dict of the hour closest to `when` (aware datetime).
    Picks forecast (recent) or archive (old) according to age."""
    now = datetime.now(UTC)
    age_days = (now - when).total_seconds() / 86400
    utc = when.astimezone(UTC)

    # Window of +-1 day, not the single UTC day: a late-evening local start falls at
    # the edge of the UTC day, and the closest hour may be on the other side of
    # midnight.
    common = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "hourly": HOURLY, "timezone": "UTC",
        "start_date": (utc - timedelta(days=1)).strftime("%Y-%m-%d"),
        "end_date": (utc + timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    # The forecast API covers the recent past via start_date/end_date (window of a few
    # months); beyond it, the archive takes over. `past_days` is incompatible with a
    # date range, so it is not used.
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
        "time_utc": times[idx],
        "temp": at("temperature_2m"),
        "feels_like": at("apparent_temperature"),
        "humidity": at("relative_humidity_2m"),
        "wind": at("wind_speed_10m"),
        "precip": at("precipitation"),
        "pressure": at("surface_pressure"),
        "radiation": at("shortwave_radiation"),
        "direct_radiation": at("direct_radiation"),
        "weather_code": at("weather_code"),
    }

    # WBGT is a reading convenience, not the data: if an input is missing or the
    # energy balance does not converge, return the weather without it rather than
    # deprive the coach of everything. A missing key can be read; an invented figure
    # cannot.
    needed = (out["temp"], out["humidity"], out["pressure"], out["wind"],
              out["radiation"])
    if all(v is not None for v in needed):
        with contextlib.suppress(WBGTError, ValueError, ZeroDivisionError,
                                 OverflowError):
            out.update(wbgt_outdoor(out["temp"], out["humidity"], out["pressure"],
                                    out["wind"], out["radiation"],
                                    lat, lon, when))
    return out


def format_summary(w, when, midpoint=None):
    parts = [sky(w)]
    if w["temp"] is not None:
        s = f"{w['temp']:.0f} °C"
        if w["feels_like"] is not None and abs(w["feels_like"] - w["temp"]) >= 1:
            s += f" (ressenti {w['feels_like']:.0f} °C)"
        parts.append(s)
    if w["humidity"] is not None:
        parts.append(f"humidité {w['humidity']:.0f} %")
    if w["wind"] is not None:
        parts.append(f"vent {w['wind']:.0f} km/h")
    if w["precip"] is not None and w["precip"] > 0:
        parts.append(f"précip {w['precip']:.1f} mm")
    if w.get("wbgt") is not None:
        parts.append(f"**WBGT {w['wbgt']:.0f} °C** ({FLAGS[w["band"]]})")
    local = when.strftime("%Y-%m-%d %H:%M %Z")
    out = f"Météo au départ ({local}) : " + ", ".join(parts) + "."

    # The start is only one point. When the middle of the route differs markedly
    # (hillsides to town, valley floor to ridge), the start weather no longer
    # describes the session and would be wrongly quoted as the weather of the run.
    if midpoint and midpoint["temp"] is not None and w["temp"] is not None:
        gap = midpoint["temp"] - w["temp"]
        if abs(gap) >= MIDPOINT_GAP_C:
            out += (
                f" ⚠ Mi-parcours {midpoint['temp']:.0f} °C "
                f"({gap:+.0f} °C) : le départ ne résume pas ce run."
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
                         "(= `start_local` de redline strava). Le décalage est "
                         "résolu depuis les coordonnées. N'ajoute jamais un `Z` "
                         "à la place.")
    ap.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = ap.parse_args()

    midpoint_point = None
    if args.polyline:
        try:
            pts = decode_polyline(args.polyline)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        if not pts:
            raise InputError("Polyline vide ou illisible.")
        lat, lon = pts[0]
        if len(pts) >= 3:
            midpoint_point = pts[len(pts) // 2]
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        raise InputError("Fournir --polyline, ou --lat et --lon.")

    try:
        when = parse_time(args.time, local=args.local)
    except ValueError as exc:
        raise InputError(str(exc)) from exc

    zone_name = None
    if args.local:
        try:
            when, zone_name = resolve_offset(lat, lon, when)
        except Exception as exc:
            raise ExternalError(f"Fuseau du lieu de départ indéterminable : {exc}") from exc

    try:
        w = get_weather(lat, lon, when)
    except Exception as exc:
        raise ExternalError(f"Météo indisponible : {exc}") from exc

    midpoint = None
    if midpoint_point:
        try:
            midpoint = get_weather(midpoint_point[0], midpoint_point[1], when)
        except Exception:
            # The second point is a convenience, not the data: its failure must never
            # deprive the coach of the start weather.
            midpoint = None

    if args.json:
        payload = {"lat": lat, "lon": lon, "timezone": zone_name, **w, "conditions": sky(w)}
        if midpoint:
            payload["midpoint"] = {"lat": midpoint_point[0], "lon": midpoint_point[1], **midpoint,
                                   "conditions": sky(midpoint)}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(format_summary(w, when, midpoint))

