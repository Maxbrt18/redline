"""`Config.from_env`: startup stops dead on an unreadable value.

This is the contract announced by the module docstring ("any unreadable value stops
startup with a clear message"). The silent alternative is the worst: a
`PROACTIVE_MORNING=7h45` falling back to a default gives a service that runs, looks healthy,
and whose trigger will never fire -- without a single log line to say so.

Each test works on an isolated environment: `monkeypatch.setenv` restores the state
afterwards, and `from_env` reads `os.environ`.
"""

import shutil
from datetime import time as dtime

import pytest
from conftest import TEST_VAULT

from redline.service.config import Config, _load_dotenv
from redline.service.routing import Route

MINIMAL = {
    "MESSENGER_PAGE_TOKEN": "page",
    "MESSENGER_VERIFY_TOKEN": "v" * 43,
    "MESSENGER_APP_SECRET": "secret",
    "MESSENGER_ALLOWED_PSID": "4242",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A bare environment: no variable from the machine's .env leaks in here."""
    for name in list(__import__("os").environ):
        if name.split("_")[0] in ("MESSENGER", "AGENT", "CLAUDE", "CODEX",
                                  "PROACTIVE", "STRAVA",
                                  "GIT", "VAULT", "TRIAGE", "DEBOUNCE", "SESSION",
                                  "STRENGTH", "COACH", "WEBHOOK", "HEALTHCHECK",
                                  "GRAPH", "STATE", "DATABASE", "ATTACHMENT", "HEALTH"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    # The vault is a temporary folder carrying the fictional instance.
    shutil.copy(TEST_VAULT / "redline.toml", tmp_path / "redline.toml")
    for key, value in MINIMAL.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    return monkeypatch


def test_the_minimal_environment_boots(env):
    config = Config.from_env()
    assert config.page_token == "page"
    assert config.agent_provider == "codex"
    assert config.routing.plan(Route.N1).primary.model == "gpt-6-luna"
    assert config.routing.plan(Route.N1).primary.effort == "high"
    assert config.routing.plan(Route.HEAVY).primary.effort == "high"
    assert config.agent_timeout == 600
    assert config.agent_timeout_deep == 1800


def test_the_service_refuses_to_start_without_its_instance(env, tmp_path):
    (tmp_path / "redline.toml").unlink()
    with pytest.raises(SystemExit, match=r"redline\.example\.toml"):
        Config.from_env()


def test_the_instance_is_read_from_the_configured_vault(env, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "redline.toml").write_text(
        (TEST_VAULT / "redline.toml").read_text(encoding="utf-8").replace(
            'place = "Paris"', 'place = "Annecy"'), encoding="utf-8")
    env.setenv("VAULT_PATH", str(vault))
    assert Config.from_env().instance.place == "Annecy"


def test_codex_has_provider_specific_model_defaults(env):
    env.setenv("AGENT_PROVIDER", "codex")
    config = Config.from_env()

    assert config.routing.plan(Route.N1).primary.model == "gpt-6-luna"
    assert config.routing.plan(Route.N2).primary.model == "gpt-6-sol"
    assert config.routing.plan(Route.TRIAGE).primary.model == "gpt-6-luna"
    assert config.routing.plan(Route.TRIAGE).primary.effort == "low"
    assert config.routing.plan(Route.HEAVY).primary.effort == "high"


def test_codex_accepts_the_same_tcp_database_as_claude(env):
    env.setenv("AGENT_PROVIDER", "codex")
    env.setenv("DATABASE_URL", "postgresql+psycopg://app:secret@127.0.0.1:5432/redline")
    assert Config.from_env().database_url.endswith("127.0.0.1:5432/redline")


def test_codex_accepts_the_local_postgres_socket(env):
    env.setenv("AGENT_PROVIDER", "codex")
    env.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:secret@/redline?host=/run/redline-postgres")
    assert Config.from_env().database_url.endswith("host=/run/redline-postgres")


def test_route_settings_override_provider_defaults(env):
    env.setenv("AGENT_PROVIDER", "codex")
    env.setenv("AGENT_N1_MODEL", "gpt-6-sol")
    env.setenv("AGENT_N1_EFFORT", "low")

    config = Config.from_env()

    assert config.routing.plan(Route.N1).primary.model == "gpt-6-sol"
    assert config.routing.plan(Route.N1).primary.effort == "low"


def test_obsolete_flat_settings_cannot_override_a_route(env):
    env.setenv("AGENT_MODEL", "gpt-6-sol")
    env.setenv("AGENT_EFFORT", "max")
    config = Config.from_env()
    assert config.routing.plan(Route.N1).primary.model == "gpt-6-luna"
    assert config.routing.plan(Route.N1).primary.effort == "high"


def test_an_unknown_agent_provider_stops_startup(env):
    env.setenv("AGENT_PROVIDER", "inconnu")
    with pytest.raises(SystemExit, match="AGENT_PROVIDER"):
        Config.from_env()


@pytest.mark.parametrize("name", ["MESSENGER_APP_SECRET", "MESSENGER_ALLOWED_PSID"])
def test_a_missing_ingress_lock_stops_startup(env, name):
    env.delenv(name)
    with pytest.raises(SystemExit, match=name):
        Config.from_env()


@pytest.mark.parametrize(("name", "raw"), [
    ("MESSENGER_VERIFY_TOKEN", "coach-sam-2026"),
    ("MESSENGER_VERIFY_TOKEN", "REMPLACE-MOI"),
    ("STRAVA_VERIFY_TOKEN", "coach-strava-2026"),
    ("STRAVA_WEBHOOK_SECRET", "court"),
])
def test_a_guessable_shared_secret_stops_startup(env, name, raw):
    """Production must not run with the example tokens shipped in Git."""
    env.setenv(name, raw)
    with pytest.raises(SystemExit, match=name):
        Config.from_env()


def test_strava_secrets_may_stay_empty(env):
    """Empty = Strava route disabled, not a weak secret."""
    config = Config.from_env()
    assert config.strava_verify_token == "" and config.strava_webhook_secret == ""
    env.setenv("STRAVA_WEBHOOK_SECRET", "s" * 43)
    assert Config.from_env().strava_webhook_secret == "s" * 43


def test_an_empty_effort_does_not_reactivate_the_cli_default(env):
    env.setenv("AGENT_N1_EFFORT", "")
    env.setenv("AGENT_HEAVY_EFFORT", "")
    config = Config.from_env()
    assert config.routing.plan(Route.N1).primary.effort == "high"
    assert config.routing.plan(Route.HEAVY).primary.effort == "high"


def test_sol_max_is_rejected_even_when_configured_for_a_fallback(env):
    env.setenv("AGENT_PROVIDER", "codex")
    env.setenv("AGENT_N3_FALLBACK_MODEL", "gpt-6-sol")
    env.setenv("AGENT_N3_FALLBACK_EFFORT", "max")
    with pytest.raises(SystemExit, match="Sol Max"):
        Config.from_env()


def test_claude_rejects_an_effort_unknown_to_its_cli(env):
    env.setenv("AGENT_PROVIDER", "claude")
    env.setenv("AGENT_N1_EFFORT", "ultra")
    with pytest.raises(SystemExit, match="invalid pour claude"):
        Config.from_env()


@pytest.mark.parametrize("name,raw", [
    ("WEBHOOK_PORT", "huit-mille"),
    ("AGENT_TIMEOUT", "10 minutes"),
    ("AGENT_TIMEOUT_DEEP", "une demi-heure"),
])
def test_an_unreadable_integer_stops_startup(env, name, raw):
    env.setenv(name, raw)
    with pytest.raises(SystemExit) as exit_info:
        Config.from_env()
    assert name in str(exit_info.value) and raw in str(exit_info.value)


def test_an_unreadable_number_stops_startup(env):
    env.setenv("PROACTIVE_MIN_GAP_HOURS", "quatre")
    with pytest.raises(SystemExit) as exit_info:
        Config.from_env()
    assert "PROACTIVE_MIN_GAP_HOURS" in str(exit_info.value)


@pytest.mark.parametrize("raw", ["7h45", "midi", "25:00", "07:60"])
def test_an_unreadable_hour_stops_startup(env, raw):
    """The trickiest case: `7h45` is what one naturally writes in French, and silently
    falling back to the default would disable the morning message."""
    env.setenv("PROACTIVE_MORNING", raw)
    with pytest.raises(SystemExit) as exit_info:
        Config.from_env()
    assert "PROACTIVE_MORNING" in str(exit_info.value)


def test_an_empty_hour_disables_its_trigger(env):
    """An EXPLICIT empty string = trigger disabled, as documented in
    .env.example. It is the switch-off mechanism, not an error."""
    env.setenv("PROACTIVE_MORNING", "")
    assert Config.from_env().proactive_morning is None


def test_an_absent_hour_keeps_its_default(env):
    assert Config.from_env().proactive_morning == dtime(7, 45)


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("oui", True), ("ON", True),
    ("false", False), ("0", False), ("non", False),
])
def test_booleans_accept_both_languages(env, raw, expected):
    env.setenv("PROACTIVE_ENABLED", raw)
    assert Config.from_env().proactive_enabled is expected


def test_an_unknown_boolean_stops_startup(env):
    env.setenv("PROACTIVE_ENABLED", "peut-être")
    with pytest.raises(SystemExit, match="PROACTIVE_ENABLED"):
        Config.from_env()


@pytest.mark.parametrize("name,raw", [
    ("WEBHOOK_PORT", "0"),
    ("AGENT_TIMEOUT", "-1"),
    ("SESSION_TTL_HOURS", "0"),
])
def test_out_of_range_values_stop_startup(env, name, raw):
    env.setenv(name, raw)
    with pytest.raises(SystemExit, match=name):
        Config.from_env()


def test_debounce_max_cannot_be_shorter_than_debounce(env):
    env.setenv("DEBOUNCE_SECONDS", "10")
    env.setenv("DEBOUNCE_MAX_SECONDS", "5")
    with pytest.raises(SystemExit, match="DEBOUNCE_MAX_SECONDS"):
        Config.from_env()


def test_attachment_total_must_cover_one_allowed_file(env):
    env.setenv("ATTACHMENT_MAX_BYTES", "2000")
    env.setenv("ATTACHMENT_TOTAL_BYTES", "1500")
    with pytest.raises(SystemExit, match="ATTACHMENT_TOTAL_BYTES"):
        Config.from_env()


def test_a_legacy_daily_proactive_cap_has_no_effect(env):
    """An old deployment variable must not resurrect a removed cap."""
    env.setenv("PROACTIVE_MAX_PER_DAY", "0")
    assert not hasattr(Config.from_env(), "proactive_max_per_day")


# ------------------------------------------------------------------- .env

def test_dotenv_fills_only_what_the_environment_lacks(env, tmp_path):
    """`setdefault` and not `set`: on the VPS, a variable passed by systemd must win
    over the file, otherwise nothing can be overridden at launch."""
    env.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    (tmp_path / ".env").write_text("CLAUDE_BIN=/autre/claude\nGIT_BRANCH=prod\n")
    _load_dotenv(tmp_path / ".env")
    config = Config.from_env()
    assert config.claude_bin == "/usr/local/bin/claude"
    assert config.git_branch == "prod"


def test_dotenv_ignores_comments_blank_lines_and_quotes(env, tmp_path):
    (tmp_path / ".env").write_text(
        '# un commentaire\n\nGIT_BRANCH="prod"\nune ligne sans egal\n'
        "GIT_REMOTE='upstream'\n")
    _load_dotenv(tmp_path / ".env")
    config = Config.from_env()
    assert config.git_branch == "prod"
    assert config.git_remote == "upstream"


def test_an_absent_dotenv_is_not_an_error(tmp_path):
    _load_dotenv(tmp_path / "pas-la.env")
