"""Chaque correction de Sam a un mode de rejeu (chantier du 2026-10-04).

Le registre `corrections-replay.json` dit comment une règle corrigée se rejoue : un
scénario du banc, du code, rien (hors banc), ou une dette `a-rejouer`. Le contenu réel
du registre se juge dans `vault-check.py` ; ici, seulement ce que le code garantit.
"""

import datetime as dt
import json

import pytest
from conftest import VAULT_ROOT, load_script

from scripts.model import correction_replay as replay

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


def test_les_trois_formes_du_journal_donnent_des_cles_uniques():
    assert [entry.key for entry in replay.entries(JOURNAL)] == KEYS
    assert replay.entries(JOURNAL)[3].date == dt.date(2026, 8, 2)


@pytest.mark.parametrize("row, message", [
    ({"correction": "x", "replay": "peut-être"}, "invalide"),
    ({"correction": "x", "replay": "scenario"}, "scenarios"),
    ({"correction": "x", "replay": "code"}, "carriers"),
    ({"correction": "x", "replay": "a-rejouer"}, "note"),
    ({"correction": "x", "replay": "hors-banc"}, "note"),
])
def test_une_entree_incomplete_est_refusee(row, message):
    with pytest.raises(replay.ReplayFormatError, match=message):
        replay.load_ledger(json.dumps([row]))


def test_une_correction_en_double_est_refusee():
    row = {"correction": "x", "replay": "hors-banc", "note": "n"}
    with pytest.raises(replay.ReplayFormatError, match="double"):
        replay.load_ledger(json.dumps([row, row]))


def _root(tmp_path, ledger):
    fixtures = tmp_path / "server" / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "corrections-replay.json").write_text(json.dumps(ledger), encoding="utf-8")
    (fixtures / "coaching-scenarios.json").write_text(json.dumps([{"id": "connu"}]),
                                                       encoding="utf-8")
    (tmp_path / "porteur.py").write_text("", encoding="utf-8")
    return tmp_path


def test_l_audit_separe_retard_orphelins_references_cassees_et_dette(tmp_path):
    root = _root(tmp_path, [
        {"correction": KEYS[1], "replay": "scenario", "scenarios": ["connu", "inconnu"]},
        {"correction": KEYS[2], "replay": "code", "carriers": ["porteur.py", "absent.py"]},
        {"correction": KEYS[3], "replay": "a-rejouer", "note": "consigne du modèle"},
        {"correction": KEYS[4], "replay": "hors-banc", "note": "objection retirée"},
        {"correction": "2026-07-01 — Disparue", "replay": "hors-banc", "note": "n"},
    ])
    found = replay.audit(root, JOURNAL, dt.date(2026, 10, 10))
    assert [c.key for c in found.unregistered] == [KEYS[0]]
    assert found.late == []
    assert found.orphans == ["2026-07-01 — Disparue"]
    assert found.broken == [f"{KEYS[1]} : scénario inconnu inconnu",
                            f"{KEYS[2]} : porteur absent absent.py"]
    assert found.owed == [KEYS[3]]


def test_une_correction_non_classee_passe_au_rouge_apres_deux_bilans(tmp_path):
    root, journal = _root(tmp_path, []), "## 2026-10-04 — Seule\n"
    assert replay.audit(root, journal, dt.date(2026, 10, 18)).late == []
    late = replay.audit(root, journal, dt.date(2026, 10, 19)).late
    assert [c.key for c in late] == ["2026-10-04 — Seule"]


def test_vault_check_rouge_sur_reference_cassee_orange_sur_dette(tmp_path):
    journal = {"coaching/corrections.md": "## 2026-10-04 — Seule\n"}
    report = vault_check.Report(quiet=True)
    root = _root(tmp_path, [{"correction": "2026-10-04 — Seule", "replay": "scenario",
                             "scenarios": ["inconnu"]}])
    vault_check.check_corrections_replay(vault_check.Vault(root, journal), report)
    assert report.failed == 1

    report = vault_check.Report(quiet=True)
    (root / replay.LEDGER).write_text(json.dumps([{
        "correction": "2026-10-04 — Seule", "replay": "a-rejouer", "note": "n"}]))
    vault_check.check_corrections_replay(vault_check.Vault(root, journal), report)
    assert (report.failed, report.warned) == (0, 1)


def test_le_registre_reel_respecte_son_schema():
    """Forme seulement : sa complétude est un contenu, jugé par vault-check."""
    replay.load_ledger((VAULT_ROOT / replay.LEDGER).read_text(encoding="utf-8"))
