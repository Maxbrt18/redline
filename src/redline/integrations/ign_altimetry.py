"""Elevation from the IGN altimetry service (Géoplateforme): RGE ALTI, 1 m where it exists.

Free and keyless, France only. A point outside the coverage comes back as NO_DATA, and the
caller decides what to fall back on. The service allows 5 requests per second from one
address; batches stay under that pace.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

from redline.model.errors import ExternalError

URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
RESOURCE = "ign_rge_alti_wld"
NO_DATA = -99999.0
BATCH = 400
MIN_INTERVAL_S = 0.25
ATTEMPTS = 3


def elevations(lats: Sequence[float], lons: Sequence[float], *,
               opener: Callable = urllib.request.urlopen,
               sleep: Callable[[float], None] = time.sleep) -> list[float | None]:
    """The terrain elevation of each point, None where the model has no data."""
    if len(lats) != len(lons):
        raise ValueError("as many latitudes as longitudes")
    out: list[float | None] = []
    for start in range(0, len(lats), BATCH):
        body = json.dumps({
            "lat": "|".join(f"{value:.7f}" for value in lats[start:start + BATCH]),
            "lon": "|".join(f"{value:.7f}" for value in lons[start:start + BATCH]),
            "resource": RESOURCE, "delimiter": "|", "zonly": "true", "indent": "false",
        }).encode()
        request = urllib.request.Request(URL, data=body,
                                         headers={"Content-Type": "application/json"})
        values = _post(request, opener, sleep)
        if len(values) != len(lats[start:start + BATCH]):
            raise ExternalError("IGN altimetry answered a different number of points")
        out.extend(None if value <= NO_DATA else float(value) for value in values)
        sleep(MIN_INTERVAL_S)
    return out


def _post(request: urllib.request.Request, opener: Callable,
          sleep: Callable[[float], None]) -> list[float]:
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            with opener(request, timeout=60) as response:
                return list(json.loads(response.read())["elevations"])
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
            last = exc
            sleep(2.0 * (attempt + 1))
    raise ExternalError(f"IGN altimetry unavailable: {last}")
