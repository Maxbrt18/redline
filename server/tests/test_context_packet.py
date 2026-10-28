import datetime as dt
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.proactive import ProactiveJob
from scripts.model.context_packet import render_context_packet


class Store:
    def observation_rows(self):
        return [{
            "occurred_on": dt.date(2026, 8, 22), "kind": "easy",
            "idempotency_key": "strava:42", "note": "facile",
            "measurements": {
                "distance_km": {"state": "known", "value": 7.2},
            },
            "pains": {"injury": {"state": "pending", "value": None}},
        }]

    def daily_rows(self):
        return [{
            "checked_on": dt.date(2026, 8, 22), "sleep": "bonne",
            "life_load": "normale", "desire": "forte", "note": "",
        }]

    def thread_rows(self):
        return [{
            "thread_key": "t1", "body": "reparler du noir", "status": "ouvert",
            "follow_up_on": None,
        }]

    def deadline_rows(self):
        return [{
            "deadline_key": "d1", "due_on": dt.date(2026, 8, 25),
            "item": "médecin", "fact_owner": "protocols.md", "status": "todo",
        }]

    def latest_capabilities(self):
        return [{
            "capability_number": 4, "slug": "nuit", "score": 2,
            "scale": 5, "status": "orange",
        }]

    def exposure_rows(self, confirmed_only=False):
        return []

    def strength_history(self):
        return [{
            "performed_on": dt.date(2026, 8, 22), "workout": "a",
            "duration_minutes": 60, "pains": {"injury": {"before": 0, "after": 1}},
            "note": "Planche unipodale trop dure, une série faite sur les deux pieds.",
            "exercises": [{
                "exercise_slug": "plank-toe", "difficulty": 8, "sets": [],
            }],
        }]


def test_n1_packet_contains_recent_facts_but_not_decision_context():
    packet = render_context_packet(Store(), "N1", dt.date(2026, 8, 23))

    assert "distance_km" not in packet and "km=7.2" in packet
    assert "attend=pain:injury" in packet
    assert "État quotidien récent" in packet
    assert "Renfo récent, déjà enregistré par la page" in packet
    assert "plank-toe=8/10" in packet
    assert "douleur injury 0->1/10" in packet
    assert "Fils ouverts" not in packet


def test_n2_packet_adds_bounded_decision_context():
    packet = render_context_packet(Store(), "N2", dt.date(2026, 8, 23))

    assert "Fils ouverts" in packet
    assert "Échéances actives" in packet
    assert "4 nuit | 2/5 orange" in packet


def test_n0_pays_no_context_packet():
    assert render_context_packet(Store(), "N0", dt.date(2026, 8, 23)) == ""


def test_n1_packet_reaches_the_actual_agent_prompt(make_bot):
    agent = FakeAgent(depth="N1")
    bot = make_bot(agent=agent)
    bot._repo.coaching_context.return_value = "[CONTEXTE TYPÉ INJECTÉ]"

    bot._respond("4242", "Quelle séance aujourd'hui ?")

    assert "CONTEXTE TYPÉ INJECTÉ" in agent.calls[-1]["message"]


def test_a_strength_report_is_forced_to_n2_and_named_as_already_persisted(make_bot):
    agent = FakeAgent(depth="N0")
    agent.classify = MagicMock(side_effect=AssertionError("triage distant inutile"))
    bot = make_bot(agent=agent)

    bot._respond(
        "4242", "Renfo terminée", message_ids=("strength:w35-mer-ef-2026-08-26",))

    message = agent.calls[-1]["message"]
    assert "NIVEAU N2" in message
    assert "RAPPORT RENFO DEJA ENREGISTRE" in message
    assert "fact-read.py strength --limit 3" in message
    assert "ne dis jamais que la sauvegarde a echoue" in message
    agent.classify.assert_not_called()


def test_the_morning_prompt_receives_the_same_context_packet(make_bot):
    bot = make_bot()
    bot._repo.coaching_context.return_value = "[CONTEXTE TYPÉ INJECTÉ]"
    job = ProactiveJob(psid="4242", kind="morning", prompt="Prépare le mot du matin")

    prompt, _, _ = bot._proactive_prompt(
        job, job.prompt, dt.datetime(2026, 8, 25, 7, 45))

    bot._repo.coaching_context.assert_called_once_with("N1")
    assert "CONTEXTE TYPÉ INJECTÉ" in prompt


def test_failed_section_is_unknown_without_losing_other_known_sections():
    from scripts.storage import StorageError

    source = Store()
    source.observation_rows = MagicMock(side_effect=StorageError('private connection detail'))
    packet = render_context_packet(source, 'N2', dt.date(2026, 8, 23))
    assert "Observations : lecture échouée, état inconnu" in packet
    assert "private connection detail" not in packet
    assert "douleur injury 0->1/10" in packet
    assert "4 nuit | 2/5 orange" in packet
    assert "Aucun état déclaré pour aujourd'hui" in packet


def test_empty_section_is_not_reported_as_a_tool_failure():
    source = Store()
    source.observation_rows = MagicMock(return_value=[])
    packet = render_context_packet(source, 'N1', dt.date(2026, 8, 22))
    assert 'Observations : aucune donnée enregistrée' in packet
    assert 'Observations : lecture échouée' not in packet
    assert "Aucun état déclaré pour aujourd'hui" not in packet


def test_failed_missing_cell_read_does_not_invent_another_questionnaire(make_bot):
    from scripts.storage import StorageError

    bot = make_bot()
    bot._repo.ledger_cells.side_effect = StorageError('unavailable')
    note = bot._missing_cells_note(dt.date(2026, 9, 5))
    assert 'ne les redemande pas' in note
    assert 'seulement si la décision en dépend' in note


def test_morning_history_survives_the_recent_activity_limit_without_mixing_series():
    def observation(day, kind, key, value=1):
        return {'occurred_on': day, 'kind': kind, 'idempotency_key': key,
                'measurements': {}, 'pains': {'injury': {'state': 'known', 'value': value}}}

    today = dt.date(2026, 9, 6)
    rows = [observation(today, 'easy', f'activity:{i}') for i in range(12)]
    rows += [observation(today - dt.timedelta(days=i), 'wake-trace', f'morning:{i}')
             for i in range(1, 11)]
    rows += [observation(today, 'wake-trace', 'current-morning'),
             observation(today + dt.timedelta(days=1), 'wake-trace', 'future-morning'),
             observation(today - dt.timedelta(days=70), 'wake-trace', 'old-morning'),
             observation(today - dt.timedelta(days=2), 'rest-reading', 'evening-rest')]
    store = Store()
    store.observation_rows = MagicMock(return_value=rows)
    packet = render_context_packet(store, 'N1', today)
    for i in range(1, 9):
        assert f'clé=morning:{i}\n' in packet
    for key in ('morning:9', 'morning:10', 'current-morning', 'future-morning',
                'old-morning', 'evening-rest'):
        assert f'clé={key}' not in packet
    store.observation_rows.assert_called_once()


def test_qualitative_morning_history_is_not_converted_into_a_numeric_score():
    store = Store()
    store.observation_rows = MagicMock(return_value=[{
        'occurred_on': dt.date(2026, 9, 5), 'kind': 'wake-trace', 'idempotency_key': 'morning:q',
        'note': 'Probablement comme hier, comparaison incertaine.',
        'measurements': {}, 'pains': {'injury': {'state': 'not_applicable'}},
    }])
    packet = render_context_packet(store, 'N1', dt.date(2026, 9, 6))
    assert 'comparaison incertaine' in packet
    assert 'douleur=' not in packet


def test_the_packet_carries_the_rules_in_force_and_flags_overdue_deadlines():
    """Le règlement compilé départage les formulations anciennes ; une échéance échue
    se voit comme telle (audit du 2026-09-27)."""
    class Store:
        def observation_rows(self):
            return []

        def daily_rows(self):
            return []

        def thread_rows(self):
            return []

        def deadline_rows(self):
            return [{"deadline_key": "verdict-cortex", "due_on": "2026-09-06",
                     "item": "confirmer le verdict oral", "fact_owner": "athlete.md",
                     "status": "todo", "blocks": "descente courue"},
                    {"deadline_key": "ferritine", "due_on": dt.date(2026, 10, 30),
                     "item": "bilan sanguin", "fact_owner": "athlete.md",
                     "status": "todo"}]

        def latest_capabilities(self):
            return []

        def exposure_rows(self, confirmed_only=False):
            return []

        def strength_history(self):
            return []

    packet = render_context_packet(Store(), "N2", dt.date(2026, 9, 27))
    assert "verdict-cortex | 2026-09-06 ÉCHUE |" in packet
    assert "ferritine | 2026-10-30 |" in packet
    assert "[RÈGLEMENT EN VIGUEUR" in packet and "po Porte :" in packet
    assert "10 %" not in packet.split("[RÈGLEMENT")[1]


def test_the_sunday_review_receives_its_computed_dossier_and_the_n2_packet(make_bot):
    """Le bilan ne refait plus de tête ce qui se calcule (audit du 2026-09-27) ; il
    reçoit aussi le règlement en vigueur et les échéances via le paquet N2."""
    bot = make_bot()
    bot._repo.coaching_context.return_value = "[CONTEXTE TYPÉ N2]"
    bot._repo.weekly_dossier.return_value = "[DOSSIER DU BILAN calculé]"
    job = ProactiveJob(psid="4242", kind="weekly", prompt="Fais le bilan du dimanche")

    prompt, _, weeks = bot._proactive_prompt(
        job, job.prompt, dt.datetime(2026, 9, 27, 19, 0))

    bot._repo.coaching_context.assert_called_once_with("N2")
    bot._repo.weekly_dossier.assert_called_once_with("2026-W39")
    assert "[DOSSIER DU BILAN calculé]" in prompt and "[CONTEXTE TYPÉ N2]" in prompt
    assert prompt.index("[DOSSIER DU BILAN") < prompt.index("Fais le bilan")
    assert weeks == ("2026-W39", "2026-W40")
