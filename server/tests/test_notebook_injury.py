"""L'écran Injury du carnet : des faits par semaine calendaire, jamais un verdict."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from redline.notebook_board import Board
from scripts.ledger import Row

TODAY = dt.date(2026, 10, 9)


def board(rows, observations=()):
    view = Board(None, None, Path('.'), TODAY, dt.datetime(2026, 10, 9, 9, tzinfo=dt.UTC))
    view._rows, view._observations = list(rows), list(observations)
    return view


def test_injury_counts_each_calendar_week_even_without_a_note():
    rows = [Row(dt.date(2026, 9, 8), 'EF', pain=0.5), Row(dt.date(2026, 9, 13), 'SL', pain=1.0),
            Row(dt.date(2026, 9, 18), 'reveil', pain=1.0), Row(dt.date(2026, 10, 3), 'SL', pain=1.5),
            Row(dt.date(2026, 10, 7), 'EF', pain=1.0), Row(dt.date(2026, 10, 7), 'EF'),
            Row(dt.date(2026, 8, 30), 'EF', pain=2.5)]
    observations = [{'kind': 'reveil', 'occurred_on': dt.date(2026, 10, 7), 'measurements': {}},
                    {'kind': 'reveil', 'occurred_on': dt.date(2026, 10, 8),
                     'measurements': {'pain': {'state': 'known', 'value': 0}}},
                    {'kind': 'reveil', 'occurred_on': dt.date(2026, 9, 20), 'measurements': {}}]
    view = board(rows, observations).injury_view()
    assert view['start'] == '2026-09-07' and view['cap'] == 2.0
    assert [(w['week'][-3:], w['peak'], w['n'], w['wake']) for w in view['weeks']] == [
        ('W37', 1.0, 2, None), ('W38', None, 0, 1.0), ('W39', None, 0, None),
        ('W40', 1.5, 1, None), ('W41', 1.0, 1, None)]
    assert [p['long'] for p in view['points']] == [False, True, True, False]
    assert view['wakes'] == [{'date': '2026-09-18', 'pain': 1.0}]
    # Les manques des deux dernières semaines seulement, du plus récent au plus ancien.
    assert view['missing'] == ['2026-10-07']
    assert [item['value'] for item in view['scale']] == [1, 2, 3]
