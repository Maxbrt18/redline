"""Les scénarios d'évaluation : forme vérifiée, et chaque correction réelle y laisse une trace.

Le banc `server/evaluate-coaching.py` est payant et hors CI ; ses fixtures, elles, se
vérifient gratuitement. Depuis le 2026-09-27, une erreur corrigée par Sam peut devenir
un scénario rejouable : le champ `origin` nomme la correction ou la décision d'où il vient.
"""

import json

from conftest import ENGINE_ROOT, VAULT_ROOT

from redline.model import correction_replay

SCENARIOS = ENGINE_ROOT / correction_replay.SCENARIOS
REQUIRED = {"id", "route", "message", "context", "understanding", "ask", "avoid",
            "posture", "ideal", "acceptance", "max_questions", "max_messages"}
ROUTES = {"N0", "N1", "N2", "N3"}
# Un cas de relecture juge une réponse déjà écrite (`evaluate-coaching.py`, branche
# `candidate`) : il n'a ni route ni idéal.
REVIEW_REQUIRED = {"id", "message", "candidate", "expected_approved"}


def _cases():
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def test_every_scenario_has_the_fields_the_bench_reads():
    for case in _cases():
        if "candidate" in case:
            missing = REVIEW_REQUIRED - case.keys()
            assert not missing, f"{case.get('id')} : {sorted(missing)}"
            continue
        missing = REQUIRED - case.keys()
        assert not missing, f"{case.get('id')} : {sorted(missing)}"
        assert case["route"] in ROUTES, case["id"]
        assert case["acceptance"], case["id"]


def test_scenario_ids_are_unique():
    ids = [case["id"] for case in _cases()]
    assert len(ids) == len(set(ids))


def test_a_scenario_born_from_a_correction_names_a_real_owner():
    born = [case for case in _cases() if "origin" in case]
    assert len(born) >= 6
    for case in born:
        path = case["origin"].split(" ")[0]
        assert (VAULT_ROOT / path).exists(), f"{case['id']} : {path}"


def test_the_long_run_scenario_forbids_any_fixed_percentage():
    """Décision de Sam du 2026-09-27 : l'idéal lui-même ne doit citer aucun pourcentage."""
    (case,) = [case for case in _cases() if case["id"] == "long-run-without-fixed-percentage"]
    assert "%" not in case["ideal"]
