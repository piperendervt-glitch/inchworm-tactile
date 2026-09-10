"""Learn the policy offline, between one play session and the next.

Nothing here runs while the game is being played. A generation is produced by
taking the weights that played, making mutated copies of them, scoring each
copy on the sessions that were recorded, and keeping the best.

    python -m creature_sim.train --logs sessions/creature/* \\
        --brain brains/ecoli-gen-003.json --out brains/ecoli-gen-004.json

The search is a (mu + lambda) evolution strategy rather than gradient descent
because there is no gradient to follow: the score comes from a whole run of a
simulation, not from a differentiable loss.
"""

import argparse
import glob
import json
import math
import multiprocessing
import random
import sys
import time
from pathlib import Path

from .ecoli.brain import EcoliBrain
from .logs import Session
from .replay import ReplayWorld, fitness

ROOT = Path(__file__).resolve().parents[1]
BRAINS = ROOT / 'brains'

DEFAULT_POPULATION = 32
DEFAULT_GENERATIONS = 20
DEFAULT_SIGMA = 0.1
# How many of the population survive to become parents.
DEFAULT_ELITE = 4
DEFAULT_CREATURES = 6
# Each candidate is run on every session with the same handful of placements,
# so a lucky spawn cannot carry a bad brain.
DEFAULT_TRIALS = 2


def flatten(brain):
    out = []
    for row in brain.w:
        out.extend(row)
    for row in brain.out:
        out.extend(row)
    return out


def unflatten(values, template):
    brain = EcoliBrain(seed=template.seed, generation=template.generation,
                       species=template.species)
    index = 0
    for row in range(len(brain.w)):
        width = len(brain.w[row])
        brain.w[row] = list(values[index:index + width])
        index += width
    for row in range(len(brain.out)):
        width = len(brain.out[row])
        brain.out[row] = list(values[index:index + width])
        index += width
    return EcoliBrain(seed=template.seed, weights=dict(w=brain.w, out=brain.out),
                      generation=template.generation, species=template.species)


def mutate(values, sigma, rng):
    return [v + rng.gauss(0.0, sigma) for v in values]


def score(args):
    """One candidate against every session. Runs in a worker process."""
    values, template_json, sessions, creatures, trials, seconds = args
    template = EcoliBrain.from_json(template_json)
    brain = unflatten(values, template)
    total = 0.0
    runs = 0
    for path in sessions:
        for trial in range(trials):
            world = ReplayWorld(path, brain=brain, count=creatures, seed=trial)
            total += fitness(world.run(seconds))
            runs += 1
    return total / max(1, runs)


class Trainer:
    def __init__(self, sessions, brain=None, population=DEFAULT_POPULATION,
                 elite=DEFAULT_ELITE, sigma=DEFAULT_SIGMA, seed=0,
                 creatures=DEFAULT_CREATURES, trials=DEFAULT_TRIALS,
                 seconds=None, workers=None, quiet=False):
        self.sessions = [str(p) for p in sessions]
        if not self.sessions:
            raise ValueError('no sessions to learn from')
        self.brain = brain if brain is not None else EcoliBrain(seed=seed)
        self.population = max(2, population)
        self.elite = max(1, min(elite, self.population - 1))
        self.sigma = sigma
        self.creatures = creatures
        self.trials = max(1, trials)
        self.seconds = seconds
        self.rng = random.Random(f'train:{seed}')
        self.workers = workers
        self.quiet = quiet
        self.history = []

    def say(self, *parts):
        if not self.quiet:
            print(*parts, flush=True)

    def evaluate(self, candidates, pool):
        template = self.brain.to_json()
        work = [(values, template, self.sessions, self.creatures, self.trials,
                 self.seconds) for values in candidates]
        if pool is None:
            return [score(item) for item in work]
        return pool.map(score, work)

    def run(self, generations=DEFAULT_GENERATIONS):
        base = flatten(self.brain)
        parents = [list(base)]
        pool = None
        if self.workers != 1:
            try:
                pool = multiprocessing.Pool(self.workers)
            except (OSError, ValueError):
                pool = None
        started = time.time()
        try:
            best_values, best_score = list(base), self.evaluate([base], pool)[0]
            self.say(f'[start] {len(self.sessions)} session(s), '
                     f'{len(base)} weights, baseline {best_score:.3f}')
            self.history.append(dict(generation=0, best=round(best_score, 4),
                                     mean=round(best_score, 4)))

            for generation in range(1, generations + 1):
                candidates = list(parents)
                while len(candidates) < self.population:
                    parent = parents[self.rng.randrange(len(parents))]
                    candidates.append(mutate(parent, self.sigma, self.rng))
                scores = self.evaluate(candidates, pool)
                ranked = sorted(zip(scores, range(len(candidates))), reverse=True)
                parents = [candidates[i] for _, i in ranked[:self.elite]]
                if ranked[0][0] > best_score:
                    best_score = ranked[0][0]
                    best_values = list(candidates[ranked[0][1]])
                # The best so far is always a parent, so a generation can
                # never be worse than the one before it.
                parents[0] = list(best_values)
                mean = sum(scores) / len(scores)
                self.history.append(dict(generation=generation,
                                         best=round(best_score, 4),
                                         mean=round(mean, 4)))
                self.say(f'[gen {generation:3d}] best {best_score:8.3f}  mean {mean:8.3f}')
        finally:
            if pool is not None:
                pool.close()
                pool.join()

        self.elapsed = time.time() - started
        learned = unflatten(best_values, self.brain)
        learned.generation = self.brain.generation + 1
        # The fingerprint is over the weights, so it has to be taken again.
        learned = EcoliBrain(seed=self.brain.seed,
                             weights=dict(w=learned.w, out=learned.out),
                             generation=self.brain.generation + 1,
                             species=self.brain.species)
        self.best_score = best_score
        return learned


def save(brain, path, trainer, parent=None):
    payload = brain.to_dict()
    payload['training'] = dict(
        parent=str(parent) if parent else None,
        parent_fingerprint=trainer.brain.fingerprint,
        sessions=trainer.sessions,
        population=trainer.population, elite=trainer.elite, sigma=trainer.sigma,
        creatures=trainer.creatures, trials=trainer.trials,
        best_fitness=round(trainer.best_score, 4),
        seconds=round(getattr(trainer, 'elapsed', 0.0), 1),
        history=trainer.history)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + '\n', encoding='utf-8')
    return path


def collect(patterns):
    """Session directories from shell patterns, skipping anything empty."""
    out = []
    for pattern in patterns:
        for match in sorted(glob.glob(pattern)):
            path = Path(match)
            if path.is_dir() and (path / 'link.jsonl').exists():
                try:
                    if Session(path).player_track():
                        out.append(path)
                except (ValueError, FileNotFoundError):
                    continue
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog='creature_sim.train',
                                description='Evolve the policy on recorded sessions')
    p.add_argument('--logs', nargs='+', required=True, help='session directories')
    p.add_argument('--brain', help='the weights to start from (default: seed 0)')
    p.add_argument('--out', required=True, help='where to write the next generation')
    p.add_argument('--generations', type=int, default=DEFAULT_GENERATIONS)
    p.add_argument('--population', type=int, default=DEFAULT_POPULATION)
    p.add_argument('--elite', type=int, default=DEFAULT_ELITE)
    p.add_argument('--sigma', type=float, default=DEFAULT_SIGMA)
    p.add_argument('--creatures', type=int, default=DEFAULT_CREATURES)
    p.add_argument('--trials', type=int, default=DEFAULT_TRIALS)
    p.add_argument('--seconds', type=float, help='cut each replay short')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--workers', type=int)
    p.add_argument('--quiet', action='store_true')
    a = p.parse_args(argv)

    sessions = collect(a.logs)
    if not sessions:
        p.error('no usable sessions matched ' + ' '.join(a.logs))
    brain = EcoliBrain.from_json(a.brain) if a.brain else EcoliBrain(seed=a.seed)
    trainer = Trainer(sessions, brain=brain, population=a.population, elite=a.elite,
                      sigma=a.sigma, seed=a.seed, creatures=a.creatures,
                      trials=a.trials, seconds=a.seconds, workers=a.workers,
                      quiet=a.quiet)
    learned = trainer.run(a.generations)
    path = save(learned, a.out, trainer, parent=a.brain)
    print(f'generation {learned.generation} -> {path}')
    print(f'fitness {trainer.history[0]["best"]:.3f} -> {trainer.best_score:.3f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
