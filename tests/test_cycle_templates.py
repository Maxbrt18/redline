"""The cycle templates, version 2: every sort parsed into frozen objects, every rule
refused with where it fails."""

from __future__ import annotations

import pytest
from conftest import TEST_VAULT

from redline.model import cycle_templates
from redline.model.cycle_templates import Range, TemplateError
from redline.model.progression import Dose
from redline.model.vocabulary import (
    CycleKind,
    DeloadMode,
    GeneralLevel,
    IntensityRef,
    PlanPhase,
    RaceTerrain,
)

SOURCE = TEST_VAULT / cycle_templates.INSTANCE_NAME
TEXT = SOURCE.read_text(encoding="utf-8")


def parse(text=TEXT):
    return cycle_templates.parse(text, SOURCE)


def refused(old, new, match, count=1):
    assert TEXT.count(old) >= count, old
    with pytest.raises(TemplateError, match=match):
        parse(TEXT.replace(old, new, count))


def test_every_sort_is_read_into_frozen_objects():
    found = parse()
    [half] = found.preparation
    assert (half.slug, half.terrain, half.taper_weeks) == ("half-road", RaceTerrain.ROAD, 2)
    assert half.deload.mode == DeloadMode.SCHEDULED and half.deload.every_weeks == Range(3, 4)
    assert [phase.name for phase in half.phases] == [
        PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC, PlanPhase.TAPER]
    development, specific, taper = half.phases
    threshold = development.keys[0]
    assert threshold.intensity.ref == IntensityRef.CRITICAL_SPEED
    assert threshold.intensity.band == Range(90, 95)
    assert threshold.start == Dose.of(reps=3, rep_seconds=480, recovery_seconds=90)
    assert specific.keys[0].flagship == 2 and specific.segments.share == Range(0.2, 0.5)
    assert taper.weeks == Range(2, 2), "the taper lasts the template's taper weeks"
    assert found.default_level == GeneralLevel.MAINTAIN
    assert found.general_at(GeneralLevel.PROGRESS).ramp_max_pct == 8
    assert found.general_at(GeneralLevel.LIGHT).keys == ()
    assert [recovery.distance_km_max for recovery in found.recovery] == [25, 400]
    assert (found.rebuild.floor_pct, found.rebuild.ramp_max_pct) == (50, 10)


def test_the_instance_file_is_read_when_it_exists(tmp_path):
    assert cycle_templates.load_instance(TEST_VAULT) == parse()
    assert cycle_templates.load_instance(tmp_path) is None


def test_the_file_says_its_version_and_declares_every_sort():
    refused("version = 2", "version = 1", "this engine reads version 2")
    refused("[[rebuild]]\nfloor_pct = 50\nramp_max_pct = 10\n", "", "'rebuild' missing")
    assert set(cycle_templates.SORTS) == set(CycleKind)


@pytest.mark.parametrize(("band", "ref", "match"), [
    ("[90, 140]", "critical_speed", r"from 50 to 130 for critical_speed"),
    ("[7, 11]", "rpe", r"from 1 to 10 for rpe"),
    ("[95, 90]", "critical_speed", r"\[95, 90\] is not a \[minimum, maximum\] range"),
])
def test_a_band_stays_within_its_reference(band, ref, match):
    refused('intensity = { ref = "critical_speed", band = [90, 95] }',
            f'intensity = {{ ref = "{ref}", band = {band} }}', match)


def test_a_dose_names_the_variables_of_the_run_ladder_and_both_doses_the_same():
    refused("start = { reps = 3, rep_seconds = 480, recovery_seconds = 90 }",
            "start = { reps = 3, distance_m = 400 }", "field 'distance_m' unknown")
    refused("start = { reps = 3, rep_seconds = 480, recovery_seconds = 90 }",
            "start = { reps = 3, rep_seconds = 480 }", "start, arrival and step name different")


def test_a_step_is_positive():
    refused("step = { reps = 1, rep_seconds = 60, recovery_seconds = 15 }",
            "step = { reps = 0, rep_seconds = 60, recovery_seconds = 15 }",
            "the step of reps is not positive")


def test_a_key_session_is_a_quality_session_of_repetitions():
    refused('workout = "hills"', 'workout = "strides"', "'strides' unknown")


def test_qualities_and_key_sessions_agree():
    refused("qualities = 1\n\n  [[preparation.taper.key]]", "qualities = 0\n\n  "
            "[[preparation.taper.key]]", "key sessions without a quality session")
    refused("min_held_km = 20", "min_held_km = 20\n\n  [[general.key]]\n  workout = "
            '"threshold"\n  intensity = { ref = "rpe", band = [6, 7] }\n  start = { reps = 1 }'
            "\n  arrival = { reps = 1 }\n  step = { reps = 1 }",
            "key sessions without a quality session")


def test_the_phases_run_in_order_each_with_its_table_and_fit_the_window():
    refused('phases = ["development", "specific", "taper"]',
            'phases = ["specific", "development", "taper"]', "in the order development")
    refused('phases = ["development", "specific", "taper"]',
            'phases = ["development", "specific"]', "the taper last")
    refused("window_weeks = [8, 12]", "window_weeks = [8, 9]",
            "the phases last 10 weeks at least, beyond the window's 9")


def test_each_deload_mode_carries_its_own_rhythm():
    refused('deload_mode = "scheduled"', 'deload_mode = "triggered"',
            "a triggered deload needs deload_days")
    refused("deload_every_weeks = [3, 4]\ndeload_cut_pct = 25",
            "deload_every_weeks = [3, 4]", "a scheduled deload needs deload_cut_pct")
    refused('deload_mode = "triggered"\ndeload_days = [2, 4]',
            'deload_mode = "triggered"\ndeload_days = [2, 4]\ndeload_every_weeks = [3, 4]',
            "deload_every_weeks does not apply to a triggered deload")


def test_only_the_progress_level_ramps():
    refused("ramp_max_pct = 8\n", "", "the progress level needs ramp_max_pct")
    refused("min_held_km = 20", "min_held_km = 20\nramp_max_pct = 5",
            "ramp_max_pct belongs to the progress level, not light")


def test_the_sorts_are_unique_and_the_default_level_exists():
    refused('level = "light"', 'level = "maintain"', "one general template per level")
    without_light = TEXT[:TEXT.index('[[general]]\nlevel = "light"')] + TEXT[
        TEXT.index("[[recovery]]"):]
    with pytest.raises(TemplateError, match="default_level light has no general template"):
        parse(without_light.replace('default_level = "maintain"', 'default_level = "light"'))
    refused("distance_km_max = 400", "distance_km_max = 25",
            "two recovery templates for 25.0 km")


def test_every_problem_is_reported_at_once():
    broken = (TEXT.replace("min_held_km = 40", "min_held_km = -1", 1)
                  .replace('default_level = "maintain"', 'default_level = "lazy"'))
    with pytest.raises(TemplateError) as raised:
        parse(broken)
    assert "min_held_km" in str(raised.value) and "'lazy' unknown" in str(raised.value)


# ------------------------------------------------------------------ vault-check


def _checked(root):
    from conftest import load_script

    vault_check = load_script("vault-check")
    report = vault_check.Report(quiet=True)
    vault_check.check_cycle_templates(vault_check.Vault(root=root, files={}), report)
    return report


def test_vault_check_reads_the_instance_templates_or_the_engine_s(tmp_path):
    assert _checked(TEST_VAULT).failed == 0
    assert _checked(tmp_path).failed == 0, "an instance without the file reads the engine's"
    (tmp_path / cycle_templates.INSTANCE_NAME).write_text(TEXT.replace("version = 2",
                                                                       "version = 1"))
    assert _checked(tmp_path).failed == 1


def test_the_engine_s_templates_serve_every_distance_and_level():
    found = cycle_templates.load(TEST_VAULT.parent / "no-vault")
    assert found.preparation_for(10, RaceTerrain.ROAD).slug == "short-road"
    assert found.preparation_for(21.1, RaceTerrain.ROAD).slug == "half-road"
    assert found.preparation_for(42.2, RaceTerrain.ROAD).slug == "marathon-road"
    assert found.preparation_for(30, RaceTerrain.TRAIL).slug == "trail"
    assert found.preparation_for(80, RaceTerrain.TRAIL).slug == "ultra"
    assert {template.level for template in found.general} == set(GeneralLevel)
    assert found.recovery_after(21.1).distance_km_max == 25
