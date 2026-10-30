"""`requirements.txt` holds the exact versions tested, so an install is reproducible."""

from conftest import ENGINE_ROOT


def test_every_requirement_is_pinned():
    lines = (ENGINE_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    loose = [line for line in lines
             if line.strip() and not line.startswith("#") and "==" not in line]
    assert not loose, loose
