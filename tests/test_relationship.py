"""Mémoire relationnelle : peu de texte, aucune question fantôme."""

from redline.service.relationship import question_fingerprint, render_memory


def test_memory_is_bounded_and_keeps_the_open_thread():
    messages = [{"role": "athlete", "body": f"message {index}"} for index in range(12)]
    rendered = render_memory(messages, [{"question": "Le rendez-vous a donné quoi ?"}])
    assert "message 3" not in rendered
    assert "message 4" in rendered and "message 11" in rendered
    assert "Questions restées ouvertes :" in rendered
    assert "Le rendez-vous a donné quoi ?" in rendered


def test_question_identity_does_not_depend_on_spacing_before_the_question_mark():
    assert question_fingerprint("La blessure ?") == question_fingerprint("La blessure?")


def test_partial_answers_and_deferred_topics_remain_explicit():
    rendered = render_memory([], [{
        "id": "q1", "question": "RPE et douleur ?", "status": "deferred",
        "asked_at": "2026-09-04", "follow_up_on": "2026-09-07",
        "review_note": "RPE déjà reçu ; douleur encore inconnue. Reprise convenue lundi.",
    }])
    assert "RPE déjà reçu" in rendered
    assert "deferred" in rendered and "2026-09-07" in rendered
    assert "jamais un agenda imposé" in rendered


def test_memory_dates_messages_and_keeps_the_end_of_a_long_answer():
    rendered = render_memory([{
        "id": "m1", "role": "athlete", "created_at": "2026-09-04T08:00:00Z",
        "body": "Contexte " * 400 + "Je préfère en reparler lundi.",
    }], [])
    assert "message_id=m1" in rendered and "2026-09-04" in rendered
    assert "extrait abrégé" in rendered and "Je préfère en reparler lundi." in rendered
    assert len(rendered) < 1500


def test_social_and_emotional_turns_do_not_inherit_a_measurement_questionnaire(make_bot):
    from redline.service.agent import Triage
    bot = make_bot()
    for triage in (Triage("N0"), Triage("N2", emotional=True)):
        note = bot._reading_notes(triage, "2026-W36", "", plan_required=False, handoff="")
        assert "Informations encore inconnues" not in note
    bot._repo.pending_ledger_cells.assert_not_called()


def test_an_acknowledgement_does_not_mark_all_pending_measurements_as_asked(make_bot, store):
    bot = make_bot()
    bot._repo.pending_ledger_cells.return_value = [{
        "date": "2026-09-05", "type": "easy", "missing": ["rpe", "pain", "enjoyment"]}]
    bot._respond("4242", "Retour sur ma séance")
    for column in ("rpe", "pain", "enjoyment"):
        assert not store.proactive.marked("asked:2026-09-05:easy:" + column)
