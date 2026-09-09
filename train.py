"""Small, reproducible evolutionary search; reward is HP survival integral only."""
import argparse
import copy
import json
from pathlib import Path
import random
from core import World, read_config, DT, LocalNCA


def evaluate(config, weights, seconds, seeds):
    scores = []
    for seed in seeds:
        cfg = copy.deepcopy(config)
        cfg['seed'] = seed
        world = World(cfg, weights)
        reward = 0.
        for _ in range(round(seconds / DT)):
            world.step()
            reward += world.hp * DT
            if world.hp <= 0:
                break
        scores.append(reward)
    return sum(scores) / len(scores)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--generations', type=int, default=10)
    parser.add_argument('--population', type=int, default=8)
    parser.add_argument('--seconds', type=float, default=45)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', default='checkpoints/best.json')
    args = parser.parse_args()
    if args.generations < 1 or args.population < 1 or args.seconds <= 0:
        parser.error('generations, population and seconds must be positive')
    cfg = read_config()
    rng = random.Random(args.seed)
    weights = LocalNCA.DEFAULT[:]
    baseline = score = evaluate(cfg, weights, args.seconds, [7, 17])
    history = []
    for generation in range(args.generations):
        candidates = [[max(-1.5, min(1.5, w + rng.gauss(0, .09))) for w in weights] for _ in range(args.population)]
        for candidate in candidates:
            result = evaluate(cfg, candidate, args.seconds, [7, 17])
            if result > score:
                weights, score = candidate, result
        history.append(score)
        print(f'generation={generation + 1} HP_integral={score:.3f} baseline={baseline:.3f}', flush=True)
    validation = evaluate(cfg, weights, args.seconds, [101, 202])
    baseline_validation = evaluate(cfg, LocalNCA.DEFAULT, args.seconds, [101, 202])
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'weights': weights, 'baseline': baseline, 'score': score, 'validation': validation, 'baseline_validation': baseline_validation, 'history': history, 'args': vars(args), 'config': cfg, 'note': 'Noise-seed validation only; not evidence of generalization to new environments.'}, indent=2))
    print(f'Saved {out}; validation={validation:.3f}, baseline_validation={baseline_validation:.3f}')


if __name__ == '__main__':
    main()
