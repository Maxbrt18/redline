"""Le banc refuse les comparaisons trompeuses et compte la forme réellement rendue."""
import json
from pathlib import Path

import pytest

from evaluation.conversation import compare_runs, emotion_note, measure, shape_checks


def test_bubbles_follow_the_transport_protocol_not_just_blank_lines():
    assert measure('Un point :\n\n- facile\n\n- court')['bubbles'] == 1
    assert measure('Oui.\n---\nÀ demain.')['bubbles'] == 2
    assert measure('')['bubbles'] == 0
    assert not shape_checks('', {'max_questions': 0, 'max_messages': 1})['nonempty']


def test_neutral_cases_do_not_receive_emotion_instructions():
    for variant in ('current', 'contextual'):
        assert emotion_note(variant, False) == ''
        assert emotion_note(variant, True)


def run(variant='current'):
    return {'variant': variant, 'fixtures_sha256': 'same', 'results': [
        {'id': 'short', 'repeat': 0, 'turn': 0, 'model': 'm', 'effort': 'low',
         'answer': 'Oui.', 'checks': {'nonempty': True}, 'scenario': {'id': 'short'},
         'history': []}]}


def test_comparison_preserves_pairs_and_does_not_declare_a_winner():
    report = compare_runs(run(), run('contextual'))
    assert report['pairs'][0]['delta_words'] == 0
    assert report['blind_review'][0]['winner'] == ''
    assert 'variant' not in report['blind_review'][0]
    assert set(report['key'][0].values()) == {1, 'current', 'contextual'}


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'error', 'model', 'fixtures'])
def test_incomplete_or_confounded_comparisons_fail(problem):
    other = run('contextual')
    if problem == 'missing':
        other['results'] = []
    elif problem == 'duplicate':
        other['results'] *= 2
    elif problem == 'error':
        other['results'][0]['error'] = 'TimeoutError'
    elif problem == 'model':
        other['results'][0]['model'] = 'another'
    else:
        other['fixtures_sha256'] = 'different'
    with pytest.raises(ValueError):
        compare_runs(run(), other)


def test_scenarios_have_reviewable_multiturn_expectations():
    path = Path(__file__).parent / 'fixtures/conversation-scenarios.json'
    cases = json.loads(path.read_text())
    assert len({case['id'] for case in cases}) == len(cases)
    assert len(cases) >= 8
    for case in cases:
        assert case['natural'] and case['artificial'] and case['today']
        assert case['turns']
        for turn in case['turns']:
            assert turn['message'] and turn['route'] in {'N0', 'N1', 'N2', 'N3'}
            assert type(turn['emotional']) is bool
            assert turn['max_questions'] >= 0 and turn['max_messages'] >= 1


@pytest.mark.parametrize(('answer', 'approved', 'expected'), [
    ('30,2 km.', True, 0), ('Tu confirmes ?', True, 1), ('', True, 1),
    ('30,2 km.', False, 1),
])
def test_historical_bench_fails_on_shape_or_review_failure(
        answer, approved, expected, monkeypatch, tmp_path, make_config):
    import runpy
    import sys
    from unittest.mock import MagicMock

    from conftest import SERVER_DIR

    bench = runpy.run_path(str(SERVER_DIR / 'evaluate-coaching.py'))
    main = bench['main']
    case = {'id': 'fact', 'message': 'Total ?', 'context': '30,2 km calculés.',
            'route': 'N1', 'emotional': False, 'max_questions': 0, 'max_messages': 1}
    fixtures = tmp_path / 'cases.json'
    fixtures.write_text(json.dumps([case]))
    runner = MagicMock()
    runner.one_shot.return_value = answer
    monkeypatch.setattr(main.__globals__['Config'], 'from_env', lambda: make_config())
    monkeypatch.setitem(main.__globals__, 'bench_runner', lambda *args: runner)
    monkeypatch.setitem(main.__globals__, 'judge', lambda *args, **kwargs: approved)
    monkeypatch.setattr(sys, 'argv', ['evaluate-coaching.py', '--fixtures', str(fixtures),
                                    '--output', str(tmp_path / 'result.json')])
    assert main() == expected
    assert '[ÉTAT D\'ESPRIT' not in runner.one_shot.call_args.kwargs['instructions']
    # Le niveau de lecture voyage avec le contexte, comme devant un message réel.
    from redline.prompts import DEPTH_NOTES
    assert DEPTH_NOTES['N1'] in json.loads(runner.one_shot.call_args.args[0])['context']
