#!/usr/bin/env python3
"""Isolated evaluation of replies: no tool, no production state, no Messenger delivery.

The fictional context explicitly replaces the reads. This is neither an end-to-end test
nor a judge of naturalness: replies must be reviewed against the fixtures' criteria.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

from redline.evaluation.conversation import shape_checks
from redline.evaluation.review import judge, review_prompt
from redline.evaluation.runner import bench_runner, check_model
from redline.model import correction_replay, paths
from redline.service.config import Config
from redline.service.prompts import DEPTH_NOTES, EMOTION_NOTE, STYLE_PROMPT, gate_note
from redline.service.relationship import render_memory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, default=paths.ENGINE_ROOT / correction_replay.SCENARIOS)
    parser.add_argument('--only', help='Identifiants séparés par des virgules')
    args = parser.parse_args()
    cases = json.loads(args.fixtures.read_text())
    if args.only:
        cases = [c for c in cases if c['id'] in args.only.split(',')]
    if not cases:
        parser.error('Aucun scénario sélectionné')
    config = Config.from_env()
    runner = bench_runner(config)  # no memory path is used
    # The judge rereads a whole reply against coaching limits: it takes the profile of
    # judgements that commit (N2), never the triage one, tuned for speed on the smallest
    # model.
    judge_spec = replace(config.routing.plan('N2').primary, timeout=300)
    results = []
    try:
        for case in cases:
            if 'candidate' in case:
                started = time.monotonic()
                spec = judge_spec
                verdict = judge(runner, review_prompt(
                    case['candidate'], case['message'], {}, today='2026-09-06'), spec=spec)
                accepted = bool(verdict)
                row = {'scenario': case, 'model': spec.model, 'effort': spec.effort,
                       'review_approved': accepted,
                       'review_reason': getattr(verdict, 'reason', None),
                       'review_detail': getattr(verdict, 'detail', ''),
                       'matches_expected': accepted == case['expected_approved'],
                       'seconds': round(time.monotonic() - started, 2)}
                results.append(row)
                args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps({'id': case['id'], 'matches_expected': row['matches_expected']}), flush=True)
                continue
            spec = config.routing.plan(case['route']).primary
            check_model(config, spec)
            started = time.monotonic()
            row = {'scenario': case, 'model': spec.model, 'effort': spec.effort}
            try:
                instructions = STYLE_PROMPT + '\n\n' + (
                    'ÉVALUATION FICTIVE : les outils sont désactivés. Le contexte fourni remplace '
                    'les lectures requises ; ne lis aucun fichier et ne tente aucune écriture. '
                    'Réponds seulement au message comme sur Messenger, sans mentionner cet exercice. '
                    'Les données manquantes restent inconnues ; aucune action réelle n’a eu lieu. '
                    'Accueille une émotion si elle est exprimée, sinon ne l’invente pas.\n'
                ) + (EMOTION_NOTE if case.get('emotional', False) else '')
                context = case['context'] + '\n' + render_memory(
                    case.get('history', []), case.get('questions', []))
                # Same as `CoachBot`: the reading-depth note, then the dated checks.
                # Without it the bench would judge a coach lacking PAIN_SCALE_POINTER
                # and the N2 instruction.
                context += '\n' + DEPTH_NOTES[case['route']]
                context += '\n' + gate_note(case.get('gates', []))
                answer = runner.one_shot(json.dumps({
                    'context': context, 'message': case['message'],
                }, ensure_ascii=False), spec=spec, instructions=instructions,
                    label='__coaching_eval__')
                row['answer'] = answer
                row['shape_checks'] = shape_checks(answer, case)
                plans = ({'2026-W36': json.dumps({'sessions': [{'date': '2026-09-06', 'distance_max_km': 8}]})}
                         if case['id'] == 'conflicting-data' else {})
                verdict = judge(runner, review_prompt(
                    answer, case['message'], plans, today='2026-09-06', context=context),
                    spec=judge_spec)
                row['review_approved'] = bool(verdict)
                row['review_reason'] = getattr(verdict, 'reason', None)
                row['review_detail'] = getattr(verdict, 'detail', '')
            except Exception as exc:
                row['error'] = type(exc).__name__
            row['seconds'] = round(time.monotonic() - started, 2)
            results.append(row)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({k: row[k] for k in ('model', 'effort', 'seconds')}
                             | {'id': case['id'], 'error': row.get('error')}, ensure_ascii=False), flush=True)
    finally:
        runner.close()
    return int(any(
        'error' in r or r.get('matches_expected') is False
        or ('shape_checks' in r and (
            not all(r['shape_checks'].values()) or not r['review_approved']))
        for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
