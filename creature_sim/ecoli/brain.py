"""Frozen policy for the giant E. coli. No learning happens here.

Weights are generated from a seed and never change during play, exactly like
``core.Policy``. The learning phase (a later stage) writes new weight files
offline; this module only loads and evaluates them.

Input layout, 100 values, matching docs/learning-monster-design.md section 14:

    0..15   pressure per tactile cell         (protocol ``cells[i].p``)
    16..31  wall contact per cell             (1.0 where ``k`` == 1)
    32..47  prey contact per cell             (1.0 where ``k`` == 2)
    48..63  food trace sampled at each cell   (stigmergy channel 0)
    64..79  damage trace sampled at each cell (stigmergy channel 2)
    80..95  death trace sampled at each cell  (stigmergy channel 3)
    96      mean path trace over the cells    (stigmergy channel 1)
    97      energy, 0..1
    98      damage taken this tick, 0..1      (max of ``cells[i].h``)
    99      1.0 if the previous action was a tumble

The three traces that say something about a place are read at every cell, so
their direction can be felt. The path trace only says how travelled a spot is,
which needs no direction, so it arrives as one number.

Contact with another creature (``k`` == 3) has no channel of its own; it
reaches the policy through the pressure value alone. Recurrent state is 8
values. Outputs are the tumble probability and the deposit strength.
"""

import hashlib
import json
import math

INPUTS = 100
STATE = 8
OUTPUTS = 2
CELLS = 16

# Offsets into the input vector, so callers never hard-code the layout.
PRESSURE = 0
WALL = 16
PREY = 32
FIELD_FOOD = 48
FIELD_DAMAGE = 64
FIELD_DEATH = 80
FIELD_PATH_MEAN = 96
ENERGY = 97
DAMAGE = 98
PREV_TUMBLE = 99

# The three traces read per cell, in the order they sit in the input vector,
# paired with the stigmergy channel each one comes from.
CELL_TRACES = ((FIELD_FOOD, 0), (FIELD_DAMAGE, 2), (FIELD_DEATH, 3))

TUMBLE = 0
DEPOSIT = 1


def _sigmoid(v):
    if v >= 0:
        return 1.0 / (1.0 + math.exp(-v))
    e = math.exp(v)
    return e / (1.0 + e)


class EcoliBrain:
    """Shared weights for a whole colony.

    Every individual runs the same policy, so the weights live here once and
    each creature carries only its own recurrent state. ``step`` is pure: it
    takes a state and returns a new one rather than mutating the brain.
    """

    def __init__(self, seed=0, weights=None, generation=0, species='ecoli'):
        if weights is None:
            if not isinstance(seed, int) or not 0 <= seed <= 2147483647:
                raise ValueError('Invalid brain seed')
            import random
            rng = random.Random(f'brain:{seed}')
            # Scaled by fan-in so the initial tanh layer is not saturated.
            spread = 0.4 / math.sqrt(INPUTS + STATE)
            self.w = [[rng.gauss(0, spread) for _ in range(INPUTS + STATE)] for _ in range(STATE)]
            self.out = [[rng.gauss(0, 0.5) for _ in range(STATE)] for _ in range(OUTPUTS)]
        else:
            self.w, self.out = self._validate(weights)
            seed = weights.get('seed', seed) if isinstance(weights, dict) else seed
        self.seed = seed
        self.generation = generation
        self.species = species
        self.fingerprint = hashlib.sha256(
            json.dumps([self.w, self.out], sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _validate(weights):
        w = weights['w'] if isinstance(weights, dict) else weights[0]
        out = weights['out'] if isinstance(weights, dict) else weights[1]
        if len(w) != STATE or any(len(row) != INPUTS + STATE for row in w):
            raise ValueError(f'w must be {STATE}x{INPUTS + STATE}')
        if len(out) != OUTPUTS or any(len(row) != STATE for row in out):
            raise ValueError(f'out must be {OUTPUTS}x{STATE}')
        for row in list(w) + list(out):
            if any(not math.isfinite(v) for v in row):
                raise ValueError('weights must be finite')
        return [list(map(float, row)) for row in w], [list(map(float, row)) for row in out]

    # -- evaluation -----------------------------------------------------

    def new_state(self):
        return [0.0] * STATE

    def step(self, inputs, state):
        """Return ``(outputs, new_state)`` without touching the brain."""
        if len(inputs) != INPUTS:
            raise ValueError(f'expected {INPUTS} inputs, got {len(inputs)}')
        if len(state) != STATE:
            raise ValueError(f'expected {STATE} state values, got {len(state)}')
        v = list(inputs) + list(state)
        new_state = [math.tanh(sum(a * b for a, b in zip(row, v))) for row in self.w]
        outputs = [_sigmoid(sum(a * b for a, b in zip(row, new_state))) for row in self.out]
        return outputs, new_state

    # -- persistence ----------------------------------------------------

    def to_dict(self):
        return dict(schema=1, species=self.species, generation=self.generation,
                    seed=self.seed, inputs=INPUTS, state=STATE, outputs=OUTPUTS,
                    fingerprint=self.fingerprint, training_enabled=False,
                    w=self.w, out=self.out)

    def to_json(self, path=None, indent=1):
        text = json.dumps(self.to_dict(), indent=indent)
        if path is not None:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
                f.write('\n')
        return text

    @classmethod
    def from_dict(cls, data):
        for key in ('inputs', 'state', 'outputs'):
            expected = dict(inputs=INPUTS, state=STATE, outputs=OUTPUTS)[key]
            if key in data and data[key] != expected:
                raise ValueError(f'{key} is {data[key]}, expected {expected}')
        brain = cls(seed=data.get('seed', 0), weights=data,
                    generation=data.get('generation', 0),
                    species=data.get('species', 'ecoli'))
        stored = data.get('fingerprint')
        if stored is not None and stored != brain.fingerprint:
            raise ValueError('fingerprint does not match the stored weights')
        return brain

    @classmethod
    def from_json(cls, source):
        text = source
        if not isinstance(source, str) or not source.lstrip().startswith('{'):
            with open(source, encoding='utf-8') as f:
                text = f.read()
        return cls.from_dict(json.loads(text))
