"""docs/configuration.md names every environment variable the engine reads."""

import re

from conftest import ENGINE_ROOT

READ = re.compile(
    r"(?:os\.environ(?:\.get)?|os\.getenv|_env(?:int|float|bool|time)?)\s*[\[(]\s*"
    r"\"([A-Z][A-Z0-9_]+)\"")
#: Read by the engine but set by the system or by the service itself, never by an operator.
INTERNAL = {"PATH", "TZ", "REDLINE_TURN_ID"}


def _read_by_the_engine() -> set[str]:
    names = set()
    for path in (ENGINE_ROOT / "src" / "redline").rglob("*.py"):
        names.update(READ.findall(path.read_text(encoding="utf-8")))
    return names - INTERNAL


def test_every_variable_read_is_documented():
    documented = (ENGINE_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    missing = sorted(name for name in _read_by_the_engine() if f"`{name}`" not in documented)
    assert not missing, missing


def test_the_scan_sees_the_service_configuration():
    """A regex that matched nothing would make the first test pass on an empty set."""
    assert {"DATABASE_URL", "MESSENGER_PAGE_TOKEN", "ATTACHMENT_DIR"} <= _read_by_the_engine()
