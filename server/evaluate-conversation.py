#!/usr/bin/env python3
"""Banc conversationnel sur plusieurs tours, isolé de PostgreSQL et de Messenger.

--dry-run ne charge ni configuration ni SDK. --compare travaille hors ligne.
La génération utilise le même adaptateur one_shot que evaluate-coaching.py ; elle
n'évalue ni les écritures, ni le triage, ni la reprise réelle d'une session du service.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from evaluation.conversation import compare_runs, digest, emotion_note, instructions, shape_checks
from redline.prompts import DEPTH_NOTES

ROOT = Path(__file__).resolve().parent.parent


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path,
                        default=ROOT / 'server/tests/fixtures/conversation-scenarios.json')
    parser.add_argument('--variant', choices=('current', 'contextual'), default='current')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', help='Identifiants séparés par des virgules')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--compare', type=Path, nargs=2, metavar=('CURRENT', 'CANDIDATE'))
    args = parser.parse_args()
    if args.compare:
        try:
            report = compare_runs(*(json.loads(p.read_text()) for p in args.compare))
        except (ValueError, KeyError) as exc:
            parser.error(str(exc))
        # Le correcteur ne reçoit pas la clé des variantes dans sa grille.
        blind = report.pop('blind_review')
        key = report.pop('key')
        write_json(args.output, report)
        write_json(args.output.with_suffix('.blind.json'), blind)
        write_json(args.output.with_suffix('.key.json'), key)
        return 0
    if args.repeat < 1:
        parser.error('--repeat doit être positif')
    raw = args.fixtures.read_text(encoding='utf-8')
    cases = json.loads(raw)
    if args.only:
        selected = set(args.only.split(','))
        unknown = selected - {c['id'] for c in cases}
        if unknown:
            parser.error('Identifiants inconnus : ' + ', '.join(sorted(unknown)))
        cases = [c for c in cases if c['id'] in selected]
    if not cases:
        parser.error('Aucun scénario')
    system = instructions(args.variant, (ROOT / 'CLAUDE.md').read_text())
    manifest = {'variant': args.variant, 'fixtures_sha256': digest(raw),
                'instructions_sha256': digest(system), 'instructions': system,
                'scope': 'Rédaction avec faits fictifs, routes imposées, sans outil.', 'results': []}
    if args.dry_run:
        manifest['scenarios'] = cases
        manifest['depth_notes'] = DEPTH_NOTES
        manifest['emotion_notes'] = {str(e): emotion_note(args.variant, e) for e in (False, True)}
        write_json(args.output, manifest)
        return 0

    from evaluation.runner import bench_runner, check_model
    from redline.config import Config

    config = Config.from_env()
    runner = bench_runner(config)
    results = []
    manifest['results'] = results
    try:
        for case in cases:
            for repeat in range(args.repeat):
                history = list(case.get('history', []))
                for turn_index, turn in enumerate(case['turns']):
                    spec = config.routing.plan(turn['route']).primary
                    check_model(config, spec)
                    prompt = json.dumps({
                        'today': case['today'], 'facts': case['context'], 'history': history,
                        'reading_note': DEPTH_NOTES[turn['route']],
                        'emotion_note': emotion_note(args.variant, turn.get('emotional', False)),
                        'message': turn['message'],
                    }, ensure_ascii=False)
                    row = {'id': case['id'], 'repeat': repeat, 'turn': turn_index,
                           'model': spec.model, 'effort': spec.effort, 'scenario': case,
                           'history': list(history), 'prompt_sha256': digest(prompt)}
                    started = time.monotonic()
                    try:
                        answer = runner.one_shot(prompt, spec=spec, instructions=system,
                                                 label='__conversation_eval__')
                        row['answer'] = answer
                        row['checks'] = shape_checks(answer, turn)
                        history.extend([{'role': 'athlete', 'body': turn['message']},
                                        {'role': 'coach', 'body': answer}])
                    except Exception as exc:
                        row['error'] = type(exc).__name__
                    row['seconds'] = round(time.monotonic() - started, 2)
                    results.append(row)
                    write_json(args.output, manifest)
                    print(json.dumps({'id': case['id'], 'repeat': repeat, 'turn': turn_index,
                                      'error': row.get('error'), 'checks': row.get('checks')},
                                     ensure_ascii=False), flush=True)
                    if 'error' in row:
                        break  # aucune suite inventée à partir d'un tour absent
    finally:
        runner.close()
    return int(any('error' in row or not all(row['checks'].values()) for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
