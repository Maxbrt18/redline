"""What the persisted plan SAYS on the athlete's phone.

The rendering is deterministic and authoritative: it must not be unreadable. It once
serialized the whole week in one sentence -- seven days, their HR zones, cadences and
internal `note` fields, dated-check identifiers included -- doubled shortly after by the
card saying the same thing better.
"""

import json

import pytest

from redline.service.plans import render_plan_delta, render_plan_for_scope
from redline.service.vault import VaultRepo

SESSIONS = [
    {"id": "w33-r1-lun-ef", "title": "EF", "date": "2026-08-10", "kind": "run",
     "workout": "easy", "status": "done", "distance_target_km": 7.0,
     "distance_cap_km": 9.0, "rpe_min": 2, "rpe_max": 3, "hr_zone": "Z2 143-157 bpm",
     "cadence_target": 168, "demanding": False, "note": "EF plat, cadence visée 168"},
    {"id": "w33-r2-mar-repos", "title": "Repos (Renfo A reporté)", "date": "2026-08-11",
     "kind": "rest", "workout": "rest", "status": "planned", "demanding": False,
     "note": "Renfo A reporté : douleur au repos supérieure à la moyenne habituelle."},
    {"id": "w33-r1-dim-sl", "title": "SL vallonnée", "date": "2026-08-16", "kind": "run",
     "workout": "long", "status": "planned", "distance_target_km": 10.0,
     "distance_cap_km": 11.0, "rpe_min": 4, "rpe_max": 4, "hr_zone": "Z2 143-157 bpm",
     "demanding": True, "note": "Premier D+ SL, D+ doux 150-250 m, terrain Hameau."},
]


@pytest.fixture
def repo(monkeypatch):
    vault = VaultRepo(path=".", remote="origin", branch="main")
    monkeypatch.setattr(vault, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 2, "sessions": SESSIONS}))
    return vault


def test_the_week_is_one_line_per_day_and_never_a_paragraph(repo):
    message = repo.weekly_plan_message("2026-W33")
    lines = message.splitlines()
    assert lines[0] == "Programme du 10/08 au 16/08 :"
    assert lines[1:] == ["lun 10/08 : EF, cible 7 km, plafond 9 km (fait)",
                         "mar 11/08 : Repos (Renfo A reporté)",
                         "mer 12/08 : rien de prévu",
                         "jeu 13/08 : rien de prévu",
                         "ven 14/08 : rien de prévu",
                         "sam 15/08 : rien de prévu",
                         "dim 16/08 : SL vallonnée, cible 10 km, plafond 11 km"]


def test_the_week_carries_no_internal_note(repo):
    """`note` is written for the archive and for the card, never for a seven-day
    summary: it is what made the message overflow."""
    message = repo.weekly_plan_message("2026-W33")
    assert "cadence visée" not in message
    assert "terrain Hameau" not in message
    assert "Z2" not in message and "RPE" not in message


def test_a_replan_shows_the_rest_of_the_week_not_the_days_already_lived(repo):
    message = repo.weekly_plan_message("2026-W33", since="2026-08-11")
    assert message.startswith("La suite de la semaine :")
    assert "lun 10/08" not in message
    assert "mar 11/08" in message and "dim 16/08" in message


def test_the_rest_of_the_week_never_contains_a_session_already_run(repo, monkeypatch):
    """A Sunday long run, run the same morning and set to `done` by the revision that
    read the activity file, was sent again in the afternoon under "the rest of the week".
    The filter only looked at the DATE, and the current date always passes.

    The two other renderings of the same object always read `status`. Only the one the
    athlete really reads, and the only one asserting "the rest", ignored it.
    """
    ran = [{**session, "status": "done"} if session["date"] == "2026-08-16" else session
           for session in SESSIONS]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 11, "sessions": ran}))
    message = repo.weekly_plan_message("2026-W33", since="2026-08-16")
    assert message == "Rien de prévu d'ici dimanche."
    assert "SL vallonnée" not in message


def test_a_cancelled_session_is_never_a_prescription(repo, monkeypatch):
    """A session removed from the week is neither prescribed nor told: `scheduled()`
    drops it from the three renderings, this one included."""
    dropped = [{**session, "status": "cancelled"} if session["date"] == "2026-08-16"
               else session for session in SESSIONS]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 3, "sessions": dropped}))
    assert "SL vallonnée" not in repo.weekly_plan_message("2026-W33")
    assert "SL vallonnée" not in repo.weekly_plan_message("2026-W33", since="2026-08-16")
    assert "SL vallonnée" not in repo.weekly_plan_message("2026-W33", today="2026-08-16")


def test_the_morning_names_a_session_already_run_rather_than_prescribing_it(repo,
                                                                            monkeypatch):
    """The morning message DESCRIBES the day: it keeps the done session, marked. It is
    "the rest of the week" that prescribes, and it alone excludes it."""
    ran = [{**session, "status": "done"} if session["date"] == "2026-08-16" else session
           for session in SESSIONS]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 11, "sessions": ran}))
    assert repo.weekly_plan_message("2026-W33", today="2026-08-16").startswith(
        "Aujourd'hui : SL vallonnée (fait), cible 10 km")


def test_the_morning_carries_everything_because_nothing_else_will(repo):
    """The morning message sends no card: this text is the only channel of the day."""
    message = repo.weekly_plan_message("2026-W33", today="2026-08-16")
    assert message == ("Aujourd'hui : SL vallonnée, cible 10 km, plafond 11 km, RPE 4-4, "
                       "Z2 143-157 bpm, Premier D+ SL, D+ doux 150-250 m, terrain Hameau.")
    # An already punctuated note does not get a second full stop.
    assert repo.weekly_plan_message("2026-W33", today="2026-08-10").endswith("cadence ~168, "
                                                                            "EF plat, cadence visée 168.")


def test_two_sessions_on_the_same_day_share_their_line(repo, monkeypatch):
    doubled = [SESSIONS[1], {**SESSIONS[0], "id": "w33-r1-mar-ef", "date": "2026-08-11"}]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 2, "sessions": doubled}))
    lines = repo.weekly_plan_message("2026-W33").splitlines()
    assert lines[2] == ("mar 11/08 : Repos (Renfo A reporté) + EF, cible 7 km, "
                        "plafond 9 km (fait)")


def test_a_gate_holds_only_the_sessions_it_names(repo):
    message = repo.weekly_plan_message(
        "2026-W33", exclude_ids=frozenset({"w33-r1-dim-sl"}))

    assert "SL vallonnée" not in message
    assert "Repos (Renfo A reporté)" in message


def test_a_gate_does_not_turn_todays_held_session_into_nothing(repo):
    """A conditional easy run still existed in the revision, but the morning rendering
    asserted "nothing planned today" after filtering it out. Waiting for an answer is
    neither a cancellation nor a prescribed rest.
    """
    message = repo.weekly_plan_message(
        "2026-W33", today="2026-08-16",
        exclude_ids=frozenset({"w33-r1-dim-sl"}))

    assert message == "Aujourd'hui : SL vallonnée, en attente de ta réponse."
    assert "Rien de prévu" not in message


def test_an_empty_scope_says_which_scope_is_empty(repo):
    assert "Rien de prévu d'ici dimanche." in repo.weekly_plan_message(
        "2026-W33", since="2026-08-17")
    assert "Rien de prévu aujourd'hui." in repo.weekly_plan_message(
        "2026-W33", today="2026-08-12")


def test_an_unreadable_context_renders_nothing_rather_than_half_a_plan(repo, monkeypatch):
    monkeypatch.setattr(repo, "weekly_plan_context", lambda week: "pas du json")
    assert repo.weekly_plan_message("2026-W33") == ""


def test_un_jour_quon_vide_se_nomme_au_lieu_de_disparaitre(repo, monkeypatch):
    """The only rendering the athlete reads kept silent on the amputated day.

    The coach cancels a strength session and forgets to create the outing meant to
    replace it. Before the fix, "the rest of the week" jumped over the day: the gap was
    invisible on BOTH sides, since the coach re-reads the same filtered list before
    writing its reply.
    """
    ampute = [{**session, "status": "cancelled"} if session["date"] == "2026-08-16"
              else session for session in SESSIONS]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 4, "sessions": ampute}))

    message = repo.weekly_plan_message("2026-W33", since="2026-08-11")

    assert "dim 16/08 : rien de prévu" in message


def test_un_jour_retenu_par_un_controle_ne_se_dit_pas_vide(repo):
    """The check question goes out in the same turn: it already says what is awaited.

    Announcing "nothing planned" instead would be a prescription, and a false one.
    """
    message = repo.weekly_plan_message(
        "2026-W33", since="2026-08-11", exclude_ids=frozenset({"w33-r1-dim-sl"}))

    assert "dim 16/08" not in message


def test_un_jour_deja_couru_ne_se_dit_pas_vide(repo, monkeypatch):
    """A day behind us has nothing left to prescribe, and is not a gap."""
    couru = [{**session, "status": "done"} if session["date"] == "2026-08-11"
             else session for session in SESSIONS]
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 5, "sessions": couru}))

    message = repo.weekly_plan_message("2026-W33", since="2026-08-11")

    assert "mar 11/08" not in message


def test_une_semaine_entierement_vide_reste_une_phrase(repo, monkeypatch):
    """Seven "nothing planned" lines would say the same thing worse."""
    monkeypatch.setattr(repo, "weekly_plan_context",
                        lambda week: json.dumps({"revision": 6, "sessions": [
                            {**SESSIONS[0], "status": "cancelled"}]}))

    assert repo.weekly_plan_message("2026-W33", since="2026-08-10") == (
        "Rien de prévu d'ici dimanche.")


def test_requested_scope_is_rendered_without_reciting_the_week():
    raw = json.dumps({"revision": 2, "sessions": SESSIONS})

    today = render_plan_for_scope(raw, "2026-W33", "today", today="2026-08-11")
    next_session = render_plan_for_scope(raw, "2026-W33", "next", today="2026-08-11")

    assert today.startswith("Aujourd'hui : Repos") and "dim 16/08" not in today
    assert next_session.startswith("Prochaine séance, mar 11/08")
    assert "dim 16/08" not in next_session


def test_reactive_revision_renders_only_its_delta():
    before = json.dumps({"revision": 2, "sessions": SESSIONS})
    changed = [{**session, "date": "2026-08-15"}
               if session["id"] == "w33-r1-dim-sl" else session for session in SESSIONS]
    after = json.dumps({"revision": 3, "sessions": changed})

    message = render_plan_delta(before, after, "2026-W33", today="2026-08-11")

    assert "SL vallonnée déplacé" in message
    assert "Repos" not in message
