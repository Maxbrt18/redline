import datetime as dt

from redline.service.race_countdown import STAGES, compile_deadline


def race(day="2026-11-26"):
    return {"id": "123", "deadline_key": "targetrace", "due_on": day,
            "item": "TargetRace", "context": {"distance_km": 45}}


def test_a_race_compiles_the_whole_countdown_at_once():
    checkpoints = compile_deadline(race())
    assert len(checkpoints) == len(STAGES)
    assert checkpoints[0].due_at.date() == dt.date(2026, 7, 29)
    assert checkpoints[-1].due_at.date() == dt.date(2026, 11, 26)
    assert checkpoints[-1].due_at.hour == 9
    assert all(checkpoint.expires_at >= checkpoint.due_at
               for checkpoint in checkpoints)


def test_job_identity_changes_when_the_race_date_moves():
    before = {row.key for row in compile_deadline(race("2026-11-26"))}
    after = {row.key for row in compile_deadline(race("2026-12-05"))}
    assert before.isdisjoint(after)


def test_only_the_contradictory_checkpoint_uses_the_heavy_premortem():
    checkpoints = compile_deadline(race())
    kinds = [row.kind for row in checkpoints]
    assert kinds.count("premortem") == 1
    assert kinds.count("race_countdown") == len(STAGES) - 1


def test_generic_checkpoints_check_a_date_instead_of_imposing_a_session():
    """A J-45 countdown required a race rehearsal that the plan places around J-25."""
    stages = {stage.key: stage for stage in STAGES}
    for key in ("systems", "simulation"):
        assert stages[key].action.startswith("Vérifier que")
    assert "datée" in stages["simulation"].evidence
