"""A fixed yardstick for a learning run: every generation, the same test.

The fitness a training run prints is measured on whatever sessions it was
given, so two generations trained on different sessions cannot be compared
by it. The autoloop therefore sets a few sessions aside when it starts, never
trains on them, and scores every generation on exactly those, with the same
placements. That is the number shown as the start, latest and best score.

    python -m creature_sim.scoreboard --scores run/scores.json \\
        --brain brains/ecoli-gen-006.json --logs sessions/autoloop/run/*

The first call fixes the benchmark (sessions, creatures, trials, seconds);
later calls reuse it and ignore what they are given, so the yardstick cannot
drift during a run.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from .ecoli.brain import EcoliBrain
from .evaluate import measure


def load(path):
    """The scores so far, or an empty record. Tolerates a missing or half-read file."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def save(path, data):
    """Write whole or not at all, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def record(path, brain_path, sessions=None, creatures=6, trials=2, seconds=None, now=None):
    """Score a brain on the benchmark and add it to the record. Returns the entry."""
    data = load(path)
    bench = data.get('benchmark')
    if bench is None:
        if not sessions:
            raise ValueError('the first score needs sessions to fix the benchmark')
        bench = dict(sessions=[str(s) for s in sessions], creatures=int(creatures),
                     trials=int(trials), seconds=seconds)
        data['benchmark'] = bench

    brain = EcoliBrain.from_json(str(brain_path))
    stats = measure(brain, [Path(s) for s in bench['sessions']], bench['creatures'],
                    bench['trials'], bench['seconds'])
    entry = dict(generation=brain.generation, fingerprint=brain.fingerprint[:12],
                 brain=str(brain_path), fitness=round(stats['fitness'], 3),
                 eating_s=round(stats['eating_s'], 3), reached=round(stats['reached'], 3),
                 contact_s=round(stats['contact_s'], 3),
                 time=(now or datetime.datetime.now()).isoformat(timespec='seconds'))

    # One entry per generation. A generation whose training found nothing
    # better has its parent's weights, and still gets its own bar.
    history = [h for h in data.get('history', [])
               if (h.get('generation'), h.get('fingerprint')) != (entry['generation'], entry['fingerprint'])]
    history.append(entry)
    data['history'] = history
    data.setdefault('start', entry)
    data['latest'] = entry
    data['best'] = max(history, key=lambda h: h['fitness'])
    save(path, data)
    return entry


def summary_lines(data):
    """What a viewer or a window shows: start, latest, best, and what changed."""
    if not data or 'start' not in data:
        return ['ベンチマーク待ち（最初の出撃を記録中）']
    start = data['start']
    latest = data.get('latest', start)
    best = data.get('best', latest)
    change = latest['fitness'] - start['fitness']
    percent = (f' ({change / abs(start["fitness"]) * 100:+.0f}%)'
               if abs(start['fitness']) > 1e-9 else '')
    return [
        f'開始  世代{start["generation"]:>3}  {start["fitness"]:9.1f}',
        f'最新  世代{latest["generation"]:>3}  {latest["fitness"]:9.1f}  {change:+.1f}{percent}',
        f'最高  世代{best["generation"]:>3}  {best["fitness"]:9.1f}',
        f'捕食  {start["eating_s"]:.1f}s → {latest["eating_s"]:.1f}s',
    ]


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.scoreboard',
                                description='Score a generation on the run\'s fixed benchmark')
    p.add_argument('--scores', required=True, help='the run\'s scores.json')
    p.add_argument('--brain', required=True)
    p.add_argument('--logs', nargs='*', help='benchmark sessions (used only by the first call)')
    p.add_argument('--creatures', type=int, default=6)
    p.add_argument('--trials', type=int, default=2)
    p.add_argument('--seconds', type=float)
    a = p.parse_args(argv)
    entry = record(a.scores, a.brain, a.logs, a.creatures, a.trials, a.seconds)
    for line in summary_lines(load(a.scores)):
        print(line)
    print(f'scored generation {entry["generation"]}: {entry["fitness"]:.3f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
