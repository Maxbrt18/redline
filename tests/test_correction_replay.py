"""Every correction has a replay mode.

The `corrections-replay.json` registry says how a corrected rule is replayed: a bench
scenario, code, nothing (outside the bench), or an `pending` debt. The real content of
the registry is judged in `vault-check`; here, only what the code guarantees.
"""

import datetime as dt
import json

import pytest
from conftest import ENGINE_ROOT, load_script

from redline.model import correction_replay as replay
from redline.model.vault_layout import CORRECTIONS_REPLAY as LEDGER

vault_check = load_script("vault-check")

JOURNAL = """## 2026-10-04 — Une correction en section

Corps.

### 2026-08-25 — Une correction en sous-section

| Date | Affirmé | Vrai | À lire | Correctif |
|---|---|---|---|---|
| 2026-08-27 (libellé) ⭐ | affirmé | vrai | lire | correctif |
| 2026-08-02 | Que la **semaine** W31 s'était bouclée sur 22 km | vrai | lire | x |
| 2026-08-02 | Que déplacer la SL au lundi | vrai | lire | x |
"""

KEYS = ["2026-10-04 — Une correction en section",
        "2026-08-25 — Une correction en sous-section",
        "2026-08-27 (libellé) ⭐",
        "2026-08-02 · Que la semaine W31 s'était bouclée sur 22",
        "2026-08-02 · Que déplacer la SL au lundi"]


def test_the_three_journal_forms_give_unique_keys():
    assert [entry.key for entry in replay.entries(JOURNAL)] == KEYS
    assert replay.entries(JOURNAL)[3].date == dt.date(2026, 8, 2)


@pytest.mark.parametrize("row, message", [
    ({"correction": "x", "replay": "peut-être"}, "invalid"),
    ({"correction": "x", "replay": "scenario"}, "scenarios"),
    ({"correction": "x", "replay": "code"}, "carriers"),
    ({"correction": "x", "replay": "pending"}, "note"),
    ({"correction": "x", "replay": "off-bench"}, "note"),
])
def test_an_incomplete_entry_is_refused(row, message):
    with pytest.raises(replay.ReplayFormatError, match=message):
        replay.load_ledger(json.dumps([row]))


def test_a_duplicate_correction_is_refused():
    row = {"correction": "x", "replay": "off-bench", "note": "n"}
    with pytest.raises(replay.ReplayFormatError, match="duplicate"):
        replay.load_ledger(json.dumps([row, row]))


def _root(tmp_path, ledger):
    """A vault holding the registry, which is also the engine holding the scenarios."""
    fixtures = tmp_path / "src" / "redline" / "evaluation"
    fixtures.mkdir(parents=True)
    (tmp_path / "coaching").mkdir()
    (tmp_path / LEDGER).write_text(json.dumps(ledger), encoding="utf-8")
    (fixtures / "coaching-scenarios.json").write_text(json.dumps([{"id": "connu"}]),
                                                       encoding="utf-8")
    (tmp_path / "porteur.py").write_text("", encoding="utf-8")
    return tmp_path


def test_the_audit_separates_lateness_orphans_broken_references_and_debt(tmp_path):
    root = _root(tmp_path, [
        {"correction": KEYS[1], "replay": "scenario", "scenarios": ["connu", "inconnu"]},
        {"correction": KEYS[2], "replay": "code", "carriers": ["porteur.py", "absent.py"]},
        {"correction": KEYS[3], "replay": "pending", "note": "consigne du modèle"},
        {"correction": KEYS[4], "replay": "off-bench", "note": "objection retirée"},
        {"correction": "2026-07-01 — Disparue", "replay": "off-bench", "note": "n"},
    ])
    found = replay.audit(root, JOURNAL, dt.date(2026, 10, 10), root)
    assert [c.key for c in found.unregistered] == [KEYS[0]]
    assert found.late == []
    assert found.orphans == ["2026-07-01 — Disparue"]
    assert found.broken == [f"{KEYS[1]}: unknown scenario inconnu",
                            f"{KEYS[2]}: carrier missing absent.py"]
    assert found.owed == [KEYS[3]]


def test_an_unclassified_correction_turns_red_after_two_reviews(tmp_path):
    root, journal = _root(tmp_path, []), "## 2026-10-04 — Seule\n"
    assert replay.audit(root, journal, dt.date(2026, 10, 18), root).late == []
    late = replay.audit(root, journal, dt.date(2026, 10, 19), root).late
    assert [c.key for c in late] == ["2026-10-04 — Seule"]


def test_vault_check_red_on_broken_reference_amber_on_debt(tmp_path):
    journal = {"coaching/corrections.md": "## 2026-10-04 — Seule\n"}
    report = vault_check.Report(quiet=True)
    root = _root(tmp_path, [{"correction": "2026-10-04 — Seule", "replay": "scenario",
                             "scenarios": ["inconnu"]}])
    vault_check.check_corrections_replay(vault_check.Vault(root, journal), report)
    assert report.failed == 1

    report = vault_check.Report(quiet=True)
    (root / LEDGER).write_text(json.dumps([{
        "correction": "2026-10-04 — Seule", "replay": "pending", "note": "n"}]))
    vault_check.check_corrections_replay(vault_check.Vault(root, journal), report)
    assert (report.failed, report.warned) == (0, 1)


def test_the_real_register_follows_its_schema():
    """Shape only: its completeness is content, judged by vault-check."""
    replay.load_ledger((ENGINE_ROOT / "tests/fixtures/corrections-replay.json")
                      .read_text(encoding="utf-8"))
