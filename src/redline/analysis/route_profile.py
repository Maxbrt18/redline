"""The elevation profile of a race route, and the climb it holds.

Method, validated against barometric altimeters on independent recordings:

- **Elevation comes from a terrain model at 1 m** (IGN RGE ALTI, `integrations.ign_altimetry`),
  sampled every STEP_M along the track, then smoothed by a Gaussian of SIGMA_M. Unsmoothed,
  the 1 m model counts micro-relief and overstates the climb by about a fifth; smoothed at
  10 to 15 m, it reproduces what a clean barometer climbs within a few percent.
- **Without that model** (outside France, offline), the GPX elevation is used as it is,
  linearly interpolated and NOT smoothed. A route planner's elevation is already a coarse,
  smooth terrain model: smoothing it again removes real climb. Its grades are less reliable.
- **D+ has no single value**: it grows with resolution. The totals of a terrain-model profile
  carry the band of the scales the validation could not separate (BAND_SIGMAS_M).

Grades are never taken point to point: GPX elevations are integers, and two points a few
metres apart turn a 1 m step into a 30 % grade.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

from redline.model.errors import InputError
from redline.model.vocabulary import ElevationSource

STEP_M = 5.0
SIGMA_M = 12.0
BAND_SIGMAS_M = (10.0, 15.0)
MAX_FILE_BYTES = 20_000_000
EARTH_RADIUS_M = 6_371_000.0

#: lat, lon -> elevation of each point, None where the terrain model has no data.
ElevationSampler = Callable[[Sequence[float], Sequence[float]], list[float | None]]


@dataclass(frozen=True)
class Track:
    name: str
    lat: np.ndarray
    lon: np.ndarray
    #: The GPX elevation of each point; None when the file carries none.
    ele: np.ndarray | None


@dataclass(frozen=True)
class Profile:
    name: str
    source: ElevationSource
    #: Distance along the track every STEP_M, in metres, and the smoothed elevation there.
    distance_m: np.ndarray
    elevation_m: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    dplus_m: float
    dminus_m: float
    #: (low, high) of the totals over BAND_SIGMAS_M; None for a GPX profile, whose band
    #: was not measured.
    dplus_band_m: tuple[float, float] | None
    dminus_band_m: tuple[float, float] | None

    @property
    def length_km(self) -> float:
        return float(self.distance_m[-1]) / 1000

    def climb(self, from_km: float, to_km: float) -> tuple[float, float]:
        """(D+, D-) between two points of the route, in metres."""
        if not 0 <= from_km < to_km <= self.length_km + 1e-9:
            raise InputError(f"km {from_km:g} to {to_km:g} is not a stretch of the route "
                             f"(0 to {self.length_km:.1f} km)")
        inside = (self.distance_m >= from_km * 1000) & (self.distance_m <= to_km * 1000)
        return _totals(self.elevation_m[inside])

    def position(self, km: float) -> tuple[float, float]:
        """(lat, lon) of a point of the route."""
        metres = min(max(km * 1000, 0.0), float(self.distance_m[-1]))
        return (float(np.interp(metres, self.distance_m, self.lat)),
                float(np.interp(metres, self.distance_m, self.lon)))

    def highest(self) -> tuple[float, float]:
        """(elevation in m, km) of the high point."""
        index = int(np.argmax(self.elevation_m))
        return float(self.elevation_m[index]), float(self.distance_m[index]) / 1000


def read_gpx(path: Path) -> Track:
    """Every track point of a GPX file, in order."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError(f"GPX unreadable: {path} ({exc.strerror})") from None
    if size > MAX_FILE_BYTES:
        raise InputError(f"GPX too large: {size} bytes (at most {MAX_FILE_BYTES})")
    return parse_gpx(path.read_bytes())


def parse_gpx(data: bytes) -> Track:
    """Every track point of a GPX document, in order; GPX 1.0 and 1.1 alike.

    The document may come from an upload: it is size-capped, and a document type
    declaration is refused outright. A GPX never needs one, and refusing it closes entity
    expansion before the parser sees it.
    """
    if len(data) > MAX_FILE_BYTES:
        raise InputError(f"GPX too large: {len(data)} bytes (at most {MAX_FILE_BYTES})")
    if b"<!doctype" in data.lower():
        raise InputError("GPX with a document type declaration: refused")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise InputError(f"GPX unreadable: {exc}") from None
    name = next((node.text or "" for node in root.iter() if _tag(node) == "name"), "").strip()
    lat, lon, ele = [], [], []
    for node in root.iter():
        if _tag(node) != "trkpt":
            continue
        try:
            lat.append(float(node.attrib["lat"]))
            lon.append(float(node.attrib["lon"]))
        except (KeyError, ValueError):
            raise InputError("GPX track point without a valid lat/lon") from None
        text = next((child.text for child in node if _tag(child) == "ele"), None)
        ele.append(float(text) if text is not None else math.nan)
    if len(lat) < 2:
        raise InputError("GPX without a track: two track points at least")
    elevations = np.array(ele)
    return Track(name, np.array(lat), np.array(lon),
                 None if np.isnan(elevations).any() else elevations)


def _tag(node: ElementTree.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _along(track: Track) -> np.ndarray:
    """Distance along the track in metres (local equirectangular projection)."""
    lat0 = math.radians(float(np.mean(track.lat)))
    x = EARTH_RADIUS_M * np.radians(track.lon) * math.cos(lat0)
    y = EARTH_RADIUS_M * np.radians(track.lat)
    return np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])


def _totals(elevation: np.ndarray) -> tuple[float, float]:
    steps = np.diff(elevation)
    return float(steps[steps > 0].sum()), float(-steps[steps < 0].sum())


def profile(track: Track, sampler: ElevationSampler | None = None) -> Profile:
    """The route profile: terrain model when `sampler` covers the whole track, else GPX."""
    along = _along(track)
    keep = np.concatenate([[True], np.diff(along) > 0])
    along, lat, lon = along[keep], track.lat[keep], track.lon[keep]
    if along[-1] < STEP_M * 2:
        raise InputError(f"track too short: {along[-1]:.0f} m")
    grid = np.append(np.arange(0.0, along[-1], STEP_M), along[-1])
    grid_lat, grid_lon = np.interp(grid, along, lat), np.interp(grid, along, lon)
    sampled = sampler(grid_lat.tolist(), grid_lon.tolist()) if sampler is not None else None
    if sampled is not None and all(value is not None for value in sampled):
        raw = np.array(sampled, dtype=float)
        smoothed = gaussian_filter1d(raw, SIGMA_M / STEP_M, mode="nearest")
        bands = [_totals(gaussian_filter1d(raw, sigma / STEP_M, mode="nearest"))
                 for sigma in BAND_SIGMAS_M]
        dplus, dminus = _totals(smoothed)
        return Profile(track.name, ElevationSource.IGN, grid, smoothed, grid_lat, grid_lon,
                       dplus, dminus,
                       (min(b[0] for b in bands), max(b[0] for b in bands)),
                       (min(b[1] for b in bands), max(b[1] for b in bands)))
    if track.ele is None:
        raise InputError("no elevation: the GPX carries none and the terrain model does not "
                         "cover the whole route")
    elevation = np.interp(grid, along, track.ele[keep])
    dplus, dminus = _totals(elevation)
    return Profile(track.name, ElevationSource.GPX, grid, elevation, grid_lat, grid_lon,
                   dplus, dminus, None, None)
