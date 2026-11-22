"""Plan templates: the phases of a preparation by race distance and terrain."""

from __future__ import annotations

import pytest

from redline.model import plan_templates
from redline.model.vocabulary import PlanPhase, RaceTerrain

TEMPLATES = plan_templates.load()


@pytest.mark.parametrize(("distance", "terrain", "slug"), [
    (10, RaceTerrain.ROAD, "short-road"), (21.1, RaceTerrain.ROAD, "half-road"),
    (42.2, RaceTerrain.ROAD, "marathon-road"), (30, RaceTerrain.TRAIL, "trail"),
    (80, RaceTerrain.TRAIL, "ultra"), (100, RaceTerrain.ROAD, "ultra"),
])
def test_a_race_is_served_by_the_first_template_of_its_distance_and_terrain(
        distance, terrain, slug):
    assert plan_templates.for_race(TEMPLATES, distance, terrain).slug == slug


def test_a_template_states_every_phase_and_the_taper_by_its_length():
    half = plan_templates.for_race(TEMPLATES, 21.1, RaceTerrain.ROAD)
    assert half.phase(PlanPhase.SPECIFIC).minimum == 5
    assert half.phase(PlanPhase.TAPER).minimum == half.taper_weeks == 2
    assert half.phase(PlanPhase.SPECIFIC).families == ("threshold",)


def test_an_instance_replaces_the_templates_as_a_whole(tmp_path):
    text = plan_templates.DEFAULT.read_text(encoding="utf-8")
    (tmp_path / plan_templates.INSTANCE_NAME).write_text(
        text.replace('slug = "half-road"', 'slug = "my-half"'), encoding="utf-8")
    assert "my-half" in {template.slug for template in plan_templates.load(tmp_path)}
    assert plan_templates.load(tmp_path / "nowhere") == TEMPLATES


@pytest.mark.parametrize(("change", "problem"), [
    (('weeks = [4, 5]', 'weeks = [5, 4]'), "is not a \\[minimum, maximum\\] range"),
    (('families = ["threshold", "strides"]', 'families = ["vma"]'), "'vma' unknown"),
    (('slug = "half-road"', 'slug = "short-road"'), "duplicate template: short-road"),
])
def test_a_template_that_cannot_be_laid_out_is_refused(tmp_path, change, problem):
    text = plan_templates.DEFAULT.read_text(encoding="utf-8").replace(*change, 1)
    with pytest.raises(plan_templates.TemplateError, match=problem):
        plan_templates.parse(text, tmp_path / "plan_templates.toml")
