"""Score several brains on the same recordings, to see whether learning took.

    python -m creature_sim.evaluate --brain brains/ecoli-gen-000.json \\
        --brain brains/ecoli-gen-005.json --logs sessions/creature/*

Every brain runs on every session with the same placements, so the numbers
are comparable rather than a matter of who got the luckier spawn.
"""

import argparse
import sys
from pathlib import Path

from .ecoli.brain import EcoliBrain
from .replay import ReplayWorld, fitness
from .train import DEFAULT_CREATURES, DEFAULT_TRIALS, collect


def measure(brain, sessions, creatures=DEFAULT_CREATURES, trials=DEFAULT_TRIALS,
            seconds=None):
    """Mean fitness and the run totals behind it."""
    scores = []
    eating = contact = path = 0.0
    reached = 0
    for session in sessions:
        for trial in range(trials):
            world = ReplayWorld(session, brain=brain, count=creatures, seed=trial)
            result = world.run(seconds)
            scores.append(fitness(result))
            eating += result['eating_s']
            contact += result['contact_s']
            path += result['path_m']
            reached += result['reached']
    runs = max(1, len(scores))
    return dict(fitness=sum(scores) / runs,
                best=max(scores) if scores else 0.0,
                worst=min(scores) if scores else 0.0,
                eating_s=eating / runs, contact_s=contact / runs,
                path_m=path / runs, reached=reached / runs, runs=runs)


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.evaluate',
                                description='Compare brains on the same recordings')
    p.add_argument('--brain', action='append', required=True,
                   help='a weight file; repeat to compare several')
    p.add_argument('--logs', nargs='+', required=True)
    p.add_argument('--creatures', type=int, default=DEFAULT_CREATURES)
    p.add_argument('--trials', type=int, default=DEFAULT_TRIALS)
    p.add_argument('--seconds', type=float)
    a = p.parse_args(argv)

    sessions = collect(a.logs)
    if not sessions:
        p.error('no usable sessions matched ' + ' '.join(a.logs))
    print(f'{len(sessions)} session(s), {a.trials} placement(s) each')
    print(f'{"brain":28s} {"gen":>4s} {"fitness":>9s} {"eating s":>9s} '
          f'{"wall s":>8s} {"path m":>8s} {"reached":>8s}')
    for path in a.brain:
        brain = EcoliBrain.from_json(path)
        stats = measure(brain, sessions, a.creatures, a.trials, a.seconds)
        print(f'{Path(path).name:28s} {brain.generation:>4d} {stats["fitness"]:>9.3f} '
              f'{stats["eating_s"]:>9.2f} {stats["contact_s"]:>8.1f} '
              f'{stats["path_m"]:>8.0f} {stats["reached"]:>8.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
