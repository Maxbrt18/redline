"""scripts/strava.py: reading Strava's payloads and keeping access alive."""

import json
import urllib.parse

import pytest

from redline.integrations import strava
from redline.model import activity as activity_model

# ------------------------------------------------------------------ cadence

def test_a_run_cadence_is_doubled_into_steps_per_minute():
    """Strava counts one foot: 82.5 there, 165 steps per minute."""
    assert strava._cadence(82.4951, on_foot=True) == {"cadence_spm": 165.0}


def test_a_trail_run_counts_feet_the_same_way():
    assert strava.parse_activity({**RUN, "sport_type": "TrailRun",
                                  "average_cadence": 80.0}).cadence_spm == 160.0


def test_a_ride_cadence_is_a_crank_and_is_never_doubled():
    assert strava._cadence(80.0, on_foot=False) == {"cadence_rpm": 80.0}


def test_an_absent_cadence_stays_absent_rather_than_becoming_zero():
    assert strava._cadence(None, on_foot=True) == {}


# ------------------------------------------------------------------ activity

RUN = {
    "id": 90000000002,
    "name": "Morning Run",
    "description": "jambes lourdes",
    "sport_type": "Run",
    # As the API sends it, `Z` included: a wall-clock time serialised as if it were UTC.
    "start_date_local": "2026-08-14T07:32:50Z",
    "distance": 6969.4,
    "moving_time": 2585,
    "elapsed_time": 2600,
    "total_elevation_gain": 64.6,
    "average_heartrate": 148.2,
    "max_heartrate": 165.0,
    "average_cadence": 82.4951,
    "map": {"summary_polyline": "g|avGsvu[ICGEO"},
}


def test_the_activity_carries_what_the_ledger_needs():
    out = strava.parse_activity(RUN)
    assert out.distance_km == 6.969
    assert out.pace_s_per_km == 371
    assert out.cadence_spm == 165.0
    assert out.elevation_gain_m == 64.6
    assert out.description == "jambes lourdes"


def test_the_activity_hands_weather_exactly_what_it_asks_for():
    """weather.py wants a polyline and a local wall-clock time, without an offset."""
    query = strava.parse_activity(RUN).weather_query
    assert query == {"polyline": "g|avGsvu[ICGEO", "time": "2026-08-14T07:32:50"}


def test_the_z_strava_glues_onto_a_local_time_is_removed():
    """Read as UTC, the start would move the weather by the local offset."""
    assert strava._wall_clock("2026-08-14T07:32:50Z") == "2026-08-14T07:32:50"
    assert strava._wall_clock("2026-08-14T07:32:50") == "2026-08-14T07:32:50"
    assert strava._wall_clock(None) is None
    assert strava.parse_activity(RUN).start_local == "2026-08-14T07:32:50"


def test_the_local_time_survives_the_guard_that_weather_actually_applies():
    from redline.integrations import weather

    query = strava.parse_activity(RUN).weather_query
    parsed = weather.parse_time(query["time"], local=True)
    assert (parsed.hour, parsed.minute) == (7, 32)
    with pytest.raises(ValueError, match="SANS fuseau"):
        weather.parse_time(RUN["start_date_local"], local=True)


def test_the_activity_command_hands_weather_its_query_and_nothing_else(monkeypatch):
    monkeypatch.setattr(strava, "call", lambda *a, **k: RUN)
    out = strava.cmd_activity(type("Args", (), {"id": "2"})())
    assert out["weather_query"] == {"polyline": "g|avGsvu[ICGEO", "time": "2026-08-14T07:32:50"}
    assert out["pace_min_per_km"] == "6:11" and "route_polyline" not in out


def test_an_indoor_activity_has_no_route_and_no_weather():
    indoor = strava.parse_activity({**RUN, "trainer": True})
    assert indoor.route_polyline is None
    assert indoor.weather_query is None


def test_a_ride_is_measured_in_speed_not_in_pace():
    ride = strava.parse_activity({"id": 1, "sport_type": "Ride", "distance": 36949.8,
                                  "moving_time": 6177, "average_cadence": 80.0})
    assert ride.pace_s_per_km is None
    out = activity_model.summary(ride)
    assert out["speed_kmh"] == 21.5
    assert "pace_min_per_km" not in out
    assert out["cadence_rpm"] == 80.0


def test_raw_values_are_never_pre_rounded():
    """ledger-add.py is the only one that rounds: two roundings in a row diverge."""
    out = strava.parse_activity(RUN)
    assert out.hr_avg_bpm == 148.2
    assert out.distance_m == 6969.4


def test_gear_distance_is_converted_from_strava_metres_once():
    out = strava.distill_gear({
        "id": "g1", "name": "Trail Shoe 3", "distance": 123456.0,
        "primary": True, "retired": False,
    })
    assert out == {
        "gear_id": "g1", "name": "Trail Shoe 3", "distance_km": 123.5,
        "primary": True, "retired": False, "brand": None, "model": None,
        "description": None,
    }


# ------------------------------------------------------------------ laps

def test_each_repetition_is_readable_one_by_one():
    laps = [
        {"name": "Lap 1", "distance": 400.0, "moving_time": 96, "average_heartrate": 160.0},
        {"name": "Lap 2", "distance": 400.0, "moving_time": 90, "average_heartrate": 172.0},
    ]
    out = activity_model.lap_summaries(strava.parse_activity({**RUN, "laps": laps}))
    assert [lap["pace_min_per_km"] for lap in out] == ["4:00", "3:45"]
    assert [lap["lap"] for lap in out] == [1, 2]


def test_laps_come_from_the_endpoint_only_when_the_field_is_absent(monkeypatch):
    calls = []

    def call(endpoint, *args):
        calls.append(endpoint)
        return {**RUN, "laps": []} if endpoint.endswith("/2") else [
            {"distance": 400.0, "moving_time": 96}]
    monkeypatch.setattr(strava, "call", call)
    assert strava.cmd_laps(type("Args", (), {"id": "2"})())["laps"] == []
    assert calls == ["/activities/2"]


# ------------------------------------------------------------------ flux

def test_streams_are_flattened_into_what_descent_already_eats(monkeypatch):
    """descent.py accepte {"altitude": [...]} depuis toujours. Strava emballe la liste
    dans un objet : l'aplatir ici évite de toucher à descent.py."""
    monkeypatch.setattr(strava, "call", lambda *a, **k: {
        "altitude": {"data": [262.9, 300.0, 262.6], "series_type": "distance"}})
    out = strava.cmd_streams(type("Args", (), {"id": "1", "keys": "altitude"})())
    assert out == {"altitude": [262.9, 300.0, 262.6]}

    from redline.analysis import descent
    assert descent.descent(175.3, json.loads(json.dumps(out))["altitude"]) == 176


# ------------------------------------------------------------------ interface

def test_the_json_flag_works_on_both_sides_of_the_subcommand():
    """`strava.py activity 42 --json` est l'ordre qu'on tape naturellement, et argparse
    le refusait : « unrecognized arguments » ne dit pas où mettre le drapeau. Ce script
    existe pour qu'un agent n'ait pas à deviner la syntaxe."""
    parser = strava.build_parser()
    assert parser.parse_args(["activity", "42", "--json"]).json
    assert parser.parse_args(["--json", "activity", "42"]).json
    assert not parser.parse_args(["activity", "42"]).json


def test_every_subcommand_has_a_handler():
    """Une sous-commande sans fonction planterait en KeyError au pire moment."""
    parser = strava.build_parser()
    declared = set(parser._subparsers._group_actions[0].choices)
    assert declared == set(strava.COMMANDS)


# ------------------------------------------------------------------ jetons

def test_a_missing_client_is_named_not_guessed(monkeypatch):
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(strava, "_read_cache", lambda path: {})
    with pytest.raises(strava.StravaError, match=r"coach-messenger\.env\.example"):
        strava.access_token()


def test_a_valid_cached_token_is_reused_without_calling_strava(monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "2")
    monkeypatch.setattr(strava.time, "time", lambda: 1000.0)
    monkeypatch.setattr(strava, "_read_cache",
                        lambda path: {"access_token": "vivant", "expires_at": 99999})

    def refuse(*args, **kwargs):
        raise AssertionError("un jeton valide ne se renouvelle pas")

    monkeypatch.setattr(strava, "_refresh", refuse)
    assert strava.access_token() == "vivant"


def test_a_token_about_to_expire_is_refreshed_before_the_call(monkeypatch):
    """Un jeton valide 40 s ne survit pas à l'appel qu'on s'apprête à faire."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "2")
    monkeypatch.setattr(strava.time, "time", lambda: 1000.0)
    monkeypatch.setattr(strava, "_read_cache",
                        lambda path: {"access_token": "mourant", "expires_at": 1040,
                                      "refresh_token": "r"})
    monkeypatch.setattr(strava, "_refresh",
                        lambda *a: {"access_token": "frais", "expires_at": 30000})
    written = {}
    monkeypatch.setattr(strava, "_write_cache", lambda path, payload: written.update(payload))
    assert strava.access_token() == "frais"
    assert written["access_token"] == "frais"


def test_a_rotated_refresh_token_is_persisted(monkeypatch):
    """Strava peut faire tourner le jeton de rafraîchissement. Ne pas écrire le nouveau,
    c'est perdre l'accès à la rotation suivante — en silence, des heures plus tard."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "2")
    monkeypatch.setattr(strava, "_read_cache", lambda path: {"refresh_token": "ancien"})
    monkeypatch.setattr(strava, "_refresh", lambda *a: {
        "access_token": "a", "refresh_token": "nouveau", "expires_at": 30000})
    written = {}
    monkeypatch.setattr(strava, "_write_cache", lambda path, payload: written.update(payload))
    strava.access_token()
    assert written["refresh_token"] == "nouveau"


def test_the_environment_is_never_a_second_owner_of_the_token(monkeypatch):
    """Le cache est le SEUL propriétaire des jetons. Une amorce dans .env deviendrait
    fausse à la première rotation, en silence, et on diagnostiquerait un accès vivant
    en lisant une valeur morte. Sans cache : pas de repli, une consigne d'amorçage."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "2")
    monkeypatch.setenv("STRAVA_REFRESH_TOKEN", "amorce-qui-ne-doit-plus-servir")
    monkeypatch.setattr(strava, "_read_cache", lambda path: {})
    monkeypatch.setattr(strava, "_refresh", lambda *a: pytest.fail(
        "l'amorce de .env a été utilisée : le jeton a de nouveau deux propriétaires"))
    with pytest.raises(strava.StravaError, match="login"):
        strava.access_token()


def test_an_unreadable_cache_is_rebuilt_rather_than_fatal(tmp_path):
    corrupt = tmp_path / "cache.json"
    corrupt.write_text("{ pas du json", encoding="utf-8")
    assert strava._read_cache(corrupt) == {}


def test_the_token_cache_is_written_private_and_atomically(tmp_path):
    path = tmp_path / "nested" / "cache.json"
    strava._write_cache(path, {"access_token": "secret", "expires_at": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "secret"
    assert path.stat().st_mode & 0o077 == 0
    assert not path.with_suffix(".tmp").exists()


# ------------------------------------------------------------------ amorçage

def _login_args():
    return type("Args", (), {"json": False})()


def test_login_reads_the_code_from_stdin_never_from_the_command_line(monkeypatch, capsys):
    """Un secret passé en argument atterrit dans ~/.bash_history : c'est comme ça que
    les identifiants Strava se sont retrouvés en clair dans cinq fichiers."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "42")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setattr("builtins.input", lambda _: "abc123")
    exchanged = {}

    class Response:
        def read(self):
            return json.dumps({"access_token": "a", "refresh_token": "r",
                               "expires_at": 30000,
                               "athlete": {"firstname": "Sam"}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):
        exchanged["body"] = request.data.decode()
        return Response()

    monkeypatch.setattr(strava.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(strava, "_write_cache", lambda path, payload: exchanged.update(payload))

    out = strava.cmd_login(_login_args())

    assert "grant_type=authorization_code" in exchanged["body"]
    assert "code=abc123" in exchanged["body"]
    assert exchanged["refresh_token"] == "r"
    assert out["autorisation"] == "enregistrée"
    # L'URL d'autorisation est affichée, et ne porte jamais le secret.
    printed = capsys.readouterr().out
    assert "strava.com/oauth/authorize" in printed
    # `activity:read_all` et pas `activity:read` : sans lui, les sorties privées sont
    # invisibles et le coach conclurait qu'une séance n'existe pas.
    assert "activity:read_all" in urllib.parse.unquote(printed)
    assert "secret" not in printed


def test_login_accepts_the_whole_redirect_url_pasted_as_is(monkeypatch):
    """Ce que l'utilisateur a réellement sous la main, c'est sa barre d'adresse.
    Exiger qu'il en extraie le code lui-même, c'est inviter l'erreur de copie."""
    monkeypatch.setenv("STRAVA_CLIENT_ID", "42")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        "builtins.input",
        lambda _: "http://localhost/exchange_token?state=&code=DEADBEEF&scope=activity:read_all")
    seen = {}

    class Response:
        def read(self):
            return json.dumps({"access_token": "a", "refresh_token": "r"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(strava.urllib.request, "urlopen",
                        lambda request, timeout=None: seen.update(body=request.data.decode())
                        or Response())
    monkeypatch.setattr(strava, "_write_cache", lambda path, payload: None)

    strava.cmd_login(_login_args())
    assert "code=DEADBEEF" in seen["body"]


def test_a_reused_authorisation_code_says_to_start_over(monkeypatch):
    """Un code d'autorisation ne sert qu'une fois : sans ce message, on retente le même
    et on conclut que les identifiants sont mauvais."""
    import urllib.error

    monkeypatch.setenv("STRAVA_CLIENT_ID", "42")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    monkeypatch.setattr("builtins.input", lambda _: "deja-servi")

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)

    monkeypatch.setattr(strava.urllib.request, "urlopen", refuse)
    with pytest.raises(strava.StravaError, match="refais l'étape 1"):
        strava.cmd_login(_login_args())


# ------------------------------------------------------------------ pannes

def test_a_revoked_authorisation_says_what_to_do(monkeypatch):
    import urllib.error

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)

    monkeypatch.setattr(strava.urllib.request, "urlopen", refuse)
    with pytest.raises(strava.StravaError, match="ré-autoriser"):
        strava._refresh("1", "2", "3")


def test_a_rate_limit_is_not_an_absent_activity(monkeypatch):
    """« Quota atteint » et « la sortie n'existe pas » appellent deux réponses
    opposées du coach : les confondre lui ferait nier une séance réelle."""
    import urllib.error

    monkeypatch.setattr(strava, "access_token", lambda **k: "t")

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(strava.urllib.request, "urlopen", refuse)
    with pytest.raises(strava.StravaError, match="quota"):
        strava.call("/activities/1")


def test_an_expired_token_is_retried_once_then_given_up(monkeypatch):
    """Un 401 se répare tout seul une fois. Deux 401 de suite sont une vraie panne, et
    boucler dessus brûlerait le quota au lieu de le dire."""
    import urllib.error

    tokens = []
    monkeypatch.setattr(strava, "access_token",
                        lambda **k: tokens.append(k.get("force_refresh")) or "t")

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(strava.urllib.request, "urlopen", refuse)
    with pytest.raises(strava.StravaError):
        strava.call("/activities/1")
    assert tokens == [False, True]


def test_a_failure_is_reported_without_ever_printing_a_secret(monkeypatch, capsys):
    monkeypatch.setattr(strava, "call",
                        lambda *a, **k: (_ for _ in ()).throw(strava.StravaError("injoignable")))
    monkeypatch.setattr("sys.argv", ["strava.py", "check"])
    assert strava.main() == 1
    captured = capsys.readouterr()
    assert "Strava indisponible" in captured.err
    assert "token" not in captured.err.lower()
