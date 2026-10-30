"""PostgreSQL trusts nobody: the socket shared with the host asks for a password."""

from conftest import ENGINE_ROOT

POSTGRES = ENGINE_ROOT / "deploy" / "postgres"


def _rules() -> list[list[str]]:
    lines = (POSTGRES / "pg_hba.conf").read_text(encoding="utf-8").splitlines()
    return [line.split() for line in lines if line.strip() and not line.startswith("#")]


def test_no_connection_is_trusted():
    assert all(rule[-1] in {"scram-sha-256", "peer"} for rule in _rules())


def test_only_the_superuser_relies_on_the_account_of_the_container():
    assert [rule[2] for rule in _rules() if rule[-1] == "peer"] == ["postgres"]


def test_the_image_uses_these_rules():
    image = (POSTGRES / "Dockerfile").read_text(encoding="utf-8")
    assert "hba_file=/etc/postgresql/pg_hba.conf" in image
