#!/usr/bin/env python3
"""L'accès Strava du coach, indépendant du moteur qui l'exécute.

Pourquoi ce script existe — le 2026-08-14, Sam a uploadé sa sortie, le débrief
proactif s'est déclenché, et le coach a rendu SILENCE. Puis, sollicité à la main
(« t'as vu ma sortie ? »), il a répondu qu'il n'avait pas ses données. La trace du
run montre un coach qui a fait tout ce qu'il pouvait : il a cherché un outil Strava
dans sa liste, greffé le dépôt à la recherche d'un script, tenté d'ouvrir la page
de l'activité, testé `STRAVA_ACCESS_TOKEN`, essayé un curl. Cinq tentatives, zéro
accès.

La cause n'était pas le coach : Strava n'existait que comme serveur MCP déclaré dans
`~/.claude.json`, donc pour le moteur Claude uniquement. Le service tournait sur
Codex (`AGENT_PROVIDER=codex`) depuis le 2026-08-12, et `~/.codex/config.toml` ne
déclare aucun MCP. **Une capacité rangée dans un fichier de configuration par moteur
disparaît au premier changement de moteur**, sans que rien ne le signale — c'est la
même leçon que le lien de renfo du 2026-08-11, à l'échelle d'une source de données.

Donc : la capacité vit dans le dépôt, en stdlib, nommée dans `CLAUDE.md` que tous les
moteurs lisent. Elle se déploie avec `git pull` et se teste comme le reste.

Usage :
    python3 scripts/strava.py check
    python3 scripts/strava.py activity 90000000002
    python3 scripts/strava.py laps 90000000002
    python3 scripts/strava.py activities --limit 5
    python3 scripts/strava.py streams 90000000002 --keys altitude | \
        python3 scripts/descent.py --dplus 64.6

Sortie : un bloc `clé: valeur` en français, ou `--json` pour la forme machine.
`streams` rend toujours du JSON à plat (`{"altitude": [...]}`) : c'est exactement ce
que `scripts/descent.py` mange, sans réemballage.

⚠ **La cadence.** Strava compte les pas d'UNE jambe en course à pied : le chiffre
utile au registre est le double (168, pas 84). Mais à vélo, `average_cadence` est
déjà un régime de pédalier en tr/min, et le doubler écrirait 160 tr/min pour un
père tranquille. Ce script double pour les sports à pied UNIQUEMENT, et nomme dans
sa sortie ce qu'il a fait (`cadence_pas_min` contre `cadence_tr_min`). La règle
« multiplier par 2 » de CLAUDE.md ne portait pas cette nuance : une règle qui vit
dans du code peut la porter, une ligne de prompt non.

⚠ **Les valeurs sont BRUTES**, non arrondies : `scripts/ledger-add.py` est le seul
qui arrondit, et deux arrondis en série ne rendent pas le même chiffre qu'un seul.

Authentification — **deux faits, deux propriétaires, aucun recouvrement** :

- l'environnement du service porte **l'identité de l'application** : `STRAVA_CLIENT_ID` et
  `STRAVA_CLIENT_SECRET`, et rien d'autre. systemd charge ce fichier, donc le sandbox
  les voit — c'est le même chemin que `DATABASE_URL`.
- `server/.strava-token.json` (0600, hors Git) porte **les jetons**, qui tournent :
  l'accès expire toutes les 6 h et se renouvelle tout seul, et Strava peut remplacer
  le jeton de rafraîchissement à cette occasion. Le cache réécrit les deux.

Il n'y a **aucune amorce de jeton dans `.env`**. Elle y a vécu jusqu'au 2026-08-14 et
c'était un second propriétaire : dès la première rotation la copie de `.env` devenait
fausse sans que rien ne le signale, et on diagnostiquait un accès vivant en lisant une
valeur morte. L'amorçage est donc un geste explicite et unique :

    python3 scripts/strava.py login

à lancer **sur la machine qui fait tourner le coach** (le cache y est écrit), dans un
terminal interactif : la commande lit le code d'autorisation sur l'entrée standard et
jamais en argument, parce qu'un secret passé en argument finit dans `~/.bash_history`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
TIMEOUT = 30

# Les sports où Strava ne compte qu'un pied. Le vélo n'y est pas, et c'est le sujet.
FOOT_SPORTS = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}

# Marge avant expiration : un jeton valide 40 s ne survit pas à l'appel qu'on
# s'apprête à faire.
EXPIRY_MARGIN = 120


class StravaError(RuntimeError):
    """Une panne d'accès Strava, dite en clair et sans jamais recopier un secret."""


class StravaNotFound(StravaError):
    """Un 404 : réessayer ne fera pas réapparaître une activité supprimée ou privée."""


# --------------------------------------------------------------------- jetons

def _cache_path() -> Path:
    override = (os.environ.get("STRAVA_TOKEN_CACHE") or "").strip()
    return Path(override) if override else ROOT / "server" / ".strava-token.json"


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Un cache illisible n'est pas une panne : il se reconstruit au refresh.
        return {}


def _write_cache(path: Path, payload: dict) -> None:
    """Écriture atomique et privée : ce fichier porte un jeton de rafraîchissement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise StravaError(
                "Strava refuse le jeton de rafraîchissement (autorisation révoquée ou "
                "identifiants faux). Il faut ré-autoriser l'application : aucun appel ne "
                "passera d'ici là."
            ) from exc
        raise StravaError(f"renouvellement du jeton refusé par Strava (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise StravaError(f"Strava injoignable pendant le renouvellement : {exc.reason}") from exc


def _app_identity() -> tuple[str, str]:
    """L'identité de l'application, seul fait Strava porté par l'environnement."""
    client_id = (os.environ.get("STRAVA_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("STRAVA_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise StravaError(
            "STRAVA_CLIENT_ID et STRAVA_CLIENT_SECRET sont absents de l'environnement : "
            "ils se renseignent dans l'environnement (cf. server/.env.example)."
        )
    return client_id, client_secret


def access_token(*, force_refresh: bool = False) -> str:
    """Un jeton d'accès valide, renouvelé et persisté au besoin.

    **Deux faits, deux propriétaires, aucun recouvrement.** L'environnement porte
    l'identité de l'application (client id et secret), qui ne tourne jamais toute
    seule. Le cache porte les jetons, qui tournent à chaque renouvellement. Le
    refresh token a longtemps vécu aux deux endroits, l'un servant d'amorce à
    l'autre : dès la première rotation la copie de `.env` devenait fausse sans que
    rien ne le dise, et on se retrouvait à lire une valeur périmée pour diagnostiquer
    un accès vivant. C'est la règle du propriétaire unique de CLAUDE.md, appliquée à
    un secret plutôt qu'à une séance.

    Conséquence assumée : sans cache, aucun repli. L'amorçage est un geste explicite,
    `strava.py login`, et le message d'erreur le nomme.
    """
    client_id, client_secret = _app_identity()

    path = _cache_path()
    cache = _read_cache(path)
    if not force_refresh:
        token = cache.get("access_token")
        expires_at = cache.get("expires_at", 0)
        if token and expires_at - EXPIRY_MARGIN > time.time():
            return str(token)

    refresh_token = str(cache.get("refresh_token") or "")
    if not refresh_token:
        raise StravaError(
            f"aucun jeton dans {path} : l'autorisation n'a jamais été faite sur cette "
            "machine, ou le cache a été effacé. Lance `python3 scripts/strava.py login` "
            "SUR LA MACHINE QUI FAIT TOURNER LE COACH, et suis les deux étapes."
        )

    fresh = _refresh(client_id, client_secret, refresh_token)
    _write_cache(path, {
        "access_token": fresh["access_token"],
        "refresh_token": fresh.get("refresh_token", refresh_token),
        "expires_at": fresh.get("expires_at", int(time.time()) + 21600),
    })
    return str(fresh["access_token"])


# ---------------------------------------------------------------------- appels

def call(endpoint: str, params: dict | None = None) -> Any:
    """Un GET authentifié, avec un seul renouvellement en cas de 401."""
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{API}{endpoint}{query}"

    for attempt in (1, 2):
        token = access_token(force_refresh=attempt == 2)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                continue  # jeton périmé côté Strava : on renouvelle et on refait UNE fois
            if exc.code == 404:
                raise StravaNotFound(
                    f"Strava ne connaît pas {endpoint} : activité supprimée, privée, "
                    "ou identifiant faux."
                ) from exc
            if exc.code == 429:
                raise StravaError(
                    "quota Strava atteint (200 requêtes / 15 min). Réessaie plus tard ; "
                    "ne conclus pas que la sortie n'existe pas."
                ) from exc
            raise StravaError(f"Strava a refusé l'appel {endpoint} (HTTP {exc.code})") from exc
        except urllib.error.URLError as exc:
            raise StravaError(f"Strava injoignable : {exc.reason}") from exc
    raise StravaError("Strava a refusé le jeton deux fois de suite")


# ------------------------------------------------------------------ distillat

def _pace(distance_m: float, moving_time_s: float) -> str | None:
    """L'allure en min/km, telle qu'elle s'écrit au registre.

    L'arrondi porte sur le TOTAL avant d'être découpé : arrondir les secondes seules
    laisse passer « 6:60 » dès que la valeur exacte dépasse 6:59,5.
    """
    if not distance_m or not moving_time_s:
        return None
    minutes, seconds = divmod(round(moving_time_s / (distance_m / 1000)), 60)
    return f"{minutes}:{seconds:02d}"


def _wall_clock(value: str | None) -> str | None:
    """L'heure de départ locale, débarrassée du `Z` que Strava colle dessus.

    ⚠ Piège de l'API, et c'est le plus coûteux du lot : `start_date_local` est une
    heure MURALE au lieu de départ, mais Strava la sérialise quand même avec un `Z`
    final — « 2026-08-14T07:32:50Z » pour un départ à 7h32 à Paris. Prise au mot, elle
    se lit UTC et décale la météo de 2 h l'été, ce qui fausse la température de 2 à
    4 °C : c'est exactement le bug du 2026-08-05 (`coaching/corrections.md`).

    `weather.py --local` REFUSE une heure qui porte un fuseau. Lui passer la valeur
    telle quelle le mettrait donc en échec à chaque sortie extérieure — et un garde-fou
    qui bloque sans ouvrir de chemin correct fabrique son contournement, ce que la règle
    de méta du 2026-08-06 nomme précisément. Le chemin correct s'ouvre ici, une fois,
    plutôt que dans la tête du coach à chaque débrief.
    """
    if not value:
        return None
    return value.removesuffix("Z")


def _duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _cadence(raw: float | None, sport: str) -> tuple[str, float] | None:
    """La cadence, nommée par son unité — le ×2 ne vaut que pour les sports à pied."""
    if raw is None:
        return None
    if sport in FOOT_SPORTS:
        return ("cadence_pas_min", round(raw * 2, 1))
    return ("cadence_tr_min", round(raw, 1))


def distill_activity(raw: dict) -> dict:
    """Ce dont le coach a besoin, et rien de plus : le brut fait 100 champs."""
    sport = raw.get("sport_type") or raw.get("type") or ""
    distance = raw.get("distance") or 0.0
    moving = raw.get("moving_time") or 0
    out: dict[str, Any] = {
        "id": raw.get("id"),
        "nom": raw.get("name"),
        "description": raw.get("description") or "",
        "sport": sport,
        "depart_local": _wall_clock(raw.get("start_date_local")),
        "distance_km": round(distance / 1000, 3),
        "temps_mouvement": _duration(moving),
        "temps_total": _duration(raw.get("elapsed_time")),
        "dplus_m": raw.get("total_elevation_gain"),
        "fc_moy": raw.get("average_heartrate"),
        "fc_max": raw.get("max_heartrate"),
        "effort_relatif": raw.get("suffer_score"),
        "calories": raw.get("calories"),
        "materiel_id": raw.get("gear_id"),
        "nb_laps": len(raw.get("laps") or []),
        # Sortie de groupe : restriction du permis EF (2026-09-27).
        "nb_athletes": raw.get("athlete_count"),
    }
    if sport in FOOT_SPORTS:
        out["allure_min_km"] = _pace(distance, moving)
    elif distance and moving:
        out["vitesse_kmh"] = round(distance / moving * 3.6, 1)

    cadence = _cadence(raw.get("average_cadence"), sport)
    if cadence:
        out[cadence[0]] = cadence[1]

    polyline = (raw.get("map") or {}).get("summary_polyline") or ""
    if polyline and not raw.get("trainer"):
        # Tout ce dont weather.py a besoin, dans l'ordre où il le demande.
        out["meteo"] = {"polyline": polyline,
                        "time": _wall_clock(raw.get("start_date_local"))}
    return out


def distill_gear(raw: dict) -> dict:
    """Compteur de vie d'une paire, exprimé une fois en kilomètres."""
    distance = raw.get("distance")
    if distance is None:
        raise StravaError("Strava ne rend aucun kilométrage pour cette paire")
    return {
        "gear_id": raw.get("id"),
        "name": raw.get("name") or raw.get("nickname"),
        "distance_km": round(float(distance) / 1000, 1),
        "primary": bool(raw.get("primary")),
        "retired": bool(raw.get("retired")),
        "brand": raw.get("brand_name"),
        "model": raw.get("model_name"),
        "description": raw.get("description"),
    }


def distill_lap(lap: dict, sport: str, index: int) -> dict:
    distance = lap.get("distance") or 0.0
    moving = lap.get("moving_time") or 0
    out: dict[str, Any] = {
        "rep": index,
        "nom": lap.get("name"),
        "distance_km": round(distance / 1000, 3),
        "temps": _duration(moving),
        "fc_moy": lap.get("average_heartrate"),
        "fc_max": lap.get("max_heartrate"),
        "dplus_m": lap.get("total_elevation_gain"),
    }
    if sport in FOOT_SPORTS:
        out["allure_min_km"] = _pace(distance, moving)
    cadence = _cadence(lap.get("average_cadence"), sport)
    if cadence:
        out[cadence[0]] = cadence[1]
    return out


# ------------------------------------------------------------------ rendu

def _render(data: Any, indent: int = 0) -> list[str]:
    """Un bloc `clé: valeur` lisible — moins cher en contexte qu'un JSON indenté."""
    pad = "  " * indent
    lines = []
    if isinstance(data, dict):
        for key, value in data.items():
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key} :")
                lines.extend(_render(value, indent + 1))
            else:
                lines.append(f"{pad}{key} : {value}")
    elif isinstance(data, list):
        for item in data:
            lines.extend(_render(item, indent))
            lines.append("")
    else:
        lines.append(f"{pad}{data}")
    return lines


def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("\n".join(line for line in _render(data)).strip())


# ------------------------------------------------------------------ commandes

def cmd_activity(args) -> Any:
    return distill_activity(call(f"/activities/{args.id}"))


def cmd_laps(args) -> Any:
    activity = call(f"/activities/{args.id}")
    sport = activity.get("sport_type") or activity.get("type") or ""
    # `is None` et pas un test de vérité : une activité sans tour rendrait une liste
    # vide, et retomber sur l'endpoint dédié pour se faire répondre la même chose
    # brûlerait un appel du quota à chaque fois.
    laps = activity.get("laps")
    if laps is None:
        laps = call(f"/activities/{args.id}/laps")
    return {
        "activite": activity.get("name"),
        "sport": sport,
        "laps": [distill_lap(lap, sport, i) for i, lap in enumerate(laps, start=1)],
    }


def cmd_activities(args) -> Any:
    params: dict[str, Any] = {"per_page": args.limit}
    if args.after:
        params["after"] = int(time.mktime(time.strptime(args.after, "%Y-%m-%d")))
    rows = call("/athlete/activities", params)
    return [{
        "id": row.get("id"),
        "nom": row.get("name"),
        "sport": row.get("sport_type") or row.get("type"),
        "depart_local": _wall_clock(row.get("start_date_local")),
        "distance_km": round((row.get("distance") or 0) / 1000, 2),
        "temps_mouvement": _duration(row.get("moving_time")),
        "dplus_m": row.get("total_elevation_gain"),
    } for row in rows]


def cmd_streams(args) -> Any:
    """Les flux, aplatis à la forme que `descent.py` accepte déjà telle quelle."""
    keys = args.keys
    raw = call(f"/activities/{args.id}/streams", {"keys": keys, "key_by_type": "true"})
    return {name: stream.get("data") for name, stream in raw.items()
            if isinstance(stream, dict)}


def cmd_zones(args) -> Any:
    return call("/athlete/zones")


def gear_rows() -> list[dict]:
    athlete = call("/athlete")
    summaries = athlete.get("shoes") or []
    return [distill_gear(call(f"/gear/{row['id']}"))
            for row in summaries if row.get("id")]


def cmd_gear(args) -> Any:
    return gear_rows()


def cmd_login(args) -> Any:
    """Amorce l'autorisation : c'est le SEUL geste qui écrit un jeton à la main.

    Le code d'autorisation se lit sur l'entrée standard, jamais en argument : un
    secret passé en argument atterrit dans `~/.bash_history`, et c'est précisément
    comme ça que les identifiants Strava se sont retrouvés en clair dans cinq
    fichiers que personne ne surveillait (ménage du 2026-08-14).
    """
    client_id, client_secret = _app_identity()
    authorize = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": "http://localhost/exchange_token",
        "response_type": "code",
        "approval_prompt": "force",
        "scope": "activity:read_all",
    })
    print("1. Ouvre cette adresse dans ton navigateur et autorise l'application :\n")
    print("   " + authorize + "\n")
    print("2. Tu seras redirigé vers une page d'erreur `localhost` : c'est normal, il")
    print("   n'y a pas de serveur là. Copie l'URL de ta barre d'adresse et colle-la")
    print("   ici, puis Entrée.\n")
    try:
        pasted = input("URL de redirection (ou le code seul) : ").strip()
    except EOFError:
        raise StravaError("rien reçu sur l'entrée standard : relance dans un terminal "
                          "interactif, la commande a besoin de te lire.") from None

    code = pasted
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if not values:
            raise StravaError("aucun paramètre `code` dans cette URL")
        code = values[0]
    if not code:
        raise StravaError("code d'autorisation vide")

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            fresh = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise StravaError(
            f"Strava refuse ce code (HTTP {exc.code}). Un code d'autorisation ne sert "
            "qu'UNE fois et expire vite : refais l'étape 1 pour en obtenir un neuf."
        ) from exc
    except urllib.error.URLError as exc:
        raise StravaError(f"Strava injoignable : {exc.reason}") from exc

    path = _cache_path()
    _write_cache(path, {
        "access_token": fresh["access_token"],
        "refresh_token": fresh["refresh_token"],
        "expires_at": fresh.get("expires_at", int(time.time()) + 21600),
    })
    athlete = fresh.get("athlete") or {}
    return {
        "autorisation": "enregistrée",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "jetons_écrits_dans": str(path),
        "rappel": "l'environnement ne doit contenir QUE STRAVA_CLIENT_ID et "
                  "STRAVA_CLIENT_SECRET : les jetons vivent ici et nulle part ailleurs.",
    }


def cmd_check(args) -> Any:
    """La preuve que la capacité est vivante — à lancer avant d'affirmer qu'elle ne l'est pas."""
    athlete = call("/athlete")
    cache = _read_cache(_cache_path())
    remaining = int(cache.get("expires_at", 0) - time.time())
    return {
        "accès": "ok",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "athlete_id": athlete.get("id"),
        "jeton_valide_encore_min": max(0, remaining // 60),
    }


COMMANDS = {
    "activity": cmd_activity,
    "laps": cmd_laps,
    "activities": cmd_activities,
    "streams": cmd_streams,
    "zones": cmd_zones,
    "gear": cmd_gear,
    "check": cmd_check,
    "login": cmd_login,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Valeurs BRUTES : c'est ledger-add.py qui arrondit, et lui seul.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="sortie machine")

    # `--json` s'accepte AVANT comme APRÈS la sous-commande. Un appelant écrit
    # naturellement `strava.py activity 42 --json`, et argparse ne l'accepte qu'en
    # tête sans ce parent : l'erreur « unrecognized arguments » ne dit pas où mettre
    # le drapeau, et ce script existe pour qu'un agent n'ait pas à deviner.
    # SUPPRESS est ce qui rend les deux ordres compatibles : sans lui, le défaut de la
    # sous-commande écraserait un `--json` déjà posé en tête.
    commun = argparse.ArgumentParser(add_help=False)
    commun.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="sortie machine")

    subparsers = parser.add_subparsers(dest="command", required=True)

    activity = subparsers.add_parser("activity", parents=[commun],
                                     help="le détail distillé d'une activité")
    activity.add_argument("id")

    laps = subparsers.add_parser("laps", parents=[commun],
                                 help="chaque répétition, une par une")
    laps.add_argument("id")

    activities = subparsers.add_parser("activities", parents=[commun],
                                       help="les activités récentes")
    activities.add_argument("--limit", type=int, default=10)
    activities.add_argument("--after", help="AAAA-MM-JJ")

    streams = subparsers.add_parser("streams", parents=[commun],
                                    help="les flux, prêts pour descent.py")
    streams.add_argument("id")
    streams.add_argument("--keys", default="altitude",
                         help="altitude, heartrate, cadence, velocity_smooth, distance, time")

    subparsers.add_parser("zones", parents=[commun],
                          help="les zones de FC de l'athlète (TTL 180 j dans athlete.md)")
    subparsers.add_parser("gear", parents=[commun],
                          help="les chaussures et leur kilométrage Strava frais")
    subparsers.add_parser("check", parents=[commun],
                          help="vérifie que l'accès Strava fonctionne")
    subparsers.add_parser("login", parents=[commun],
                          help="autorise l'application et écrit les jetons (une fois)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        emit(COMMANDS[args.command](args), args.json or args.command == "streams")
    except StravaError as exc:
        print(f"Strava indisponible : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
