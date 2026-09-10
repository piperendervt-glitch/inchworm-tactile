"""Several giant E. coli sharing one stigmergy field.

The Unity side (Body) owns the physical truth: position, heading, health and
who is touching what. This module owns the decisions: the run/tumble state
machine, the energy budget and what each individual writes into the field.
``step`` consumes an ``observe`` message and returns a ``command`` message, as
defined in docs/creature-protocol.md.

There is no learning here. Weights are fixed for the whole session.
"""

import math
import random

from ..field import DAMAGE as DAMAGE_TRACE, DEATH, FOOD, PATH, StigmergyField
from .brain import (CELL_TRACES, CELLS, DAMAGE, DEPOSIT, EAR_COUNT, EARS,
                    ENERGY, FIELD_PATH_MEAN, INPUTS, PREV_TUMBLE, PREY,
                    PRESSURE, TUMBLE, WALL, EcoliBrain)

# Physiology, carried over from core.World so both models stay comparable.
DEFAULTS = dict(
    decision_seconds=0.25,
    initial_energy=100.0,
    max_energy=100.0,
    basal_cost=0.1,
    motion_cost=0.2,
    food_per_s=12.0,
    tumble_min=0.25,
    tumble_max=0.8,
    # Body geometry and top speed, overridden by the ``hello`` message.
    length=3.0,
    radius=0.8,
    run_speed=6.0,
    # How much each individual writes into the field per second. The brain
    # scales the food trace; the path trace is laid down at a fixed rate.
    deposit_food=6.0,
    deposit_path=1.0,
    # A hit and a death mark the ground as somewhere dangerous. Death is
    # written once and heavily; being shot is written for as long as it lasts.
    deposit_damage=8.0,
    deposit_death=40.0,
    # Field value that reads as 1.0 at the input layer.
    field_scale=4.0,
)

KIND_NONE, KIND_WALL, KIND_PREY, KIND_OTHER = 0, 1, 2, 3


class Creature:
    """State the Brain owns for one individual."""

    def __init__(self, creature_id, brain, settings, rng):
        self.id = creature_id
        self.rng = rng
        self.state = brain.new_state()
        self.energy = float(settings['initial_energy'])
        self.action = 'run'
        self.prev_tumble = 0.0
        self.turn_left = 0.0
        self.turn_rate = 0.0
        self.decision_left = 0.0
        self.deposit = 0.0
        self.tumble_probability = 0.5
        self.starved = False
        self.decisions = 0
        self.tumbles = 0
        self.eaten = 0.0
        self.alive_seconds = 0.0
        self.eating_seconds = 0.0


class Colony:
    def __init__(self, brain=None, field=None, bounds=None, seed=0, settings=None):
        self.brain = brain if brain is not None else EcoliBrain(seed=seed)
        self.settings = dict(DEFAULTS)
        self.settings.update(settings or {})
        for key in ('decision_seconds', 'max_energy', 'food_per_s', 'field_scale'):
            if not math.isfinite(self.settings[key]) or self.settings[key] <= 0:
                raise ValueError(key + ' must be positive')
        if field is None:
            if bounds is None:
                raise ValueError('Colony needs a field or bounds')
            field = StigmergyField(bounds)
        self.field = field
        self.seed = seed
        self.rng = random.Random(f'colony:{seed}')
        self.creatures = {}
        self.tick = 0
        self.time = 0.0
        self.deaths = 0

    # -- geometry -------------------------------------------------------

    def cell_world_xz(self, x, z, heading, index):
        """World XZ of tactile cell ``index`` (docs/creature-protocol.md 5).

        ``index = segment * 8 + ring``. Segment 0 is the front ring, 1 the
        rear. Ring 0 points up, 2 to the creature's right, 4 down, 6 left, so
        the offset is ``up * cos(phi) + right * sin(phi)`` with
        ``phi = ring * 45 degrees``. Only the right component moves the point
        in XZ, so the up and down cells read the same spot on the field.
        """
        segment, ring = divmod(index, 8)
        phi = ring * math.pi / 4.0
        axial = (self.settings['length'] / 4.0) * (1.0 if segment == 0 else -1.0)
        lateral = self.settings['radius'] * math.sin(phi)
        fx, fz = math.sin(heading), math.cos(heading)
        rx, rz = math.sin(heading + math.pi / 2.0), math.cos(heading + math.pi / 2.0)
        return x + fx * axial + rx * lateral, z + fz * axial + rz * lateral

    # -- sensing --------------------------------------------------------

    def build_inputs(self, creature, observed):
        x, _, z = observed['pos']
        heading = float(observed['heading'])
        cells = observed['cells']
        if len(cells) != CELLS:
            raise ValueError(f'expected {CELLS} cells, got {len(cells)}')
        scale = self.settings['field_scale']
        inputs = [0.0] * INPUTS
        hit = 0.0
        path_total = 0.0
        for i, cell in enumerate(cells):
            pressure = float(cell.get('p', 0.0))
            kind = int(cell.get('k', KIND_NONE))
            h = float(cell.get('h', 0.0))
            inputs[PRESSURE + i] = min(1.0, max(0.0, pressure))
            if kind == KIND_WALL:
                inputs[WALL + i] = 1.0
            elif kind == KIND_PREY:
                inputs[PREY + i] = 1.0
            if h > hit:
                hit = h
            cx, cz = self.cell_world_xz(x, z, heading, i)
            # The traces that mark a place are read at every cell, so a
            # gradient across the skin gives a direction to move in.
            for offset, channel in CELL_TRACES:
                inputs[offset + i] = min(1.0, self.field.sample(cx, cz, channel) / scale)
            path_total += min(1.0, self.field.sample(cx, cz, PATH) / scale)
        inputs[FIELD_PATH_MEAN] = path_total / CELLS
        inputs[ENERGY] = min(1.0, max(0.0, creature.energy / self.settings['max_energy']))
        inputs[DAMAGE] = min(1.0, max(0.0, hit))
        inputs[PREV_TUMBLE] = creature.prev_tumble
        # Hearing is passed straight through. A Body that has no ears (an
        # older build, a test) simply leaves the creature deaf.
        ears = observed.get('ears') or ()
        for i in range(min(EAR_COUNT, len(ears))):
            inputs[EARS + i] = min(1.0, max(0.0, float(ears[i])))
        return inputs, hit

    # -- stepping -------------------------------------------------------

    def step(self, observation):
        """Consume an ``observe`` message, return a ``command`` message."""
        dt = float(observation.get('dt', 1.0 / 30.0))
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be nonnegative and finite')
        dt = min(dt, 0.1)
        self.tick = int(observation.get('tick', self.tick + 1))
        self.time = float(observation.get('t', self.time + dt))

        seen = set()
        out = []
        for observed in observation.get('creatures', []):
            creature_id = observed['id']
            state = observed.get('state', 'alive')
            if state == 'dead':
                # Shot dead is worth remembering. Written once, where it fell,
                # before the individual is let go.
                if observed.get('reason') == 'shot':
                    pos = observed.get('pos') or [0.0, 0.0, 0.0]
                    self.field.deposit(float(pos[0]), float(pos[2]), DEATH,
                                       self.settings['deposit_death'])
                    self.deaths += 1
                self.creatures.pop(creature_id, None)
                continue
            if state == 'spawned' or creature_id not in self.creatures:
                self.creatures[creature_id] = Creature(
                    creature_id, self.brain, self.settings,
                    random.Random(f'creature:{self.seed}:{creature_id}:{self.tick}'))
            seen.add(creature_id)
            out.append(self._step_one(self.creatures[creature_id], observed, dt))

        for creature_id in list(self.creatures):
            if creature_id not in seen:
                del self.creatures[creature_id]

        self.field.update(dt)
        return dict(v=1, type='command', session=observation.get('session'),
                    tick=self.tick, creatures=out)

    def _step_one(self, creature, observed, dt):
        settings = self.settings
        x, _, z = observed['pos']
        heading = float(observed['heading'])
        speed = float(observed.get('speed', 0.0))
        eating = bool(observed.get('eating', False))
        creature.alive_seconds += dt

        inputs, hit = self.build_inputs(creature, observed)
        if hit > 0.0:
            self.field.deposit(x, z, DAMAGE_TRACE, settings['deposit_damage'] * hit)

        # Decide only every decision_seconds, and never mid-tumble.
        if creature.turn_left <= 0.0 and creature.decision_left <= 0.0 and not creature.starved:
            outputs, creature.state = self.brain.step(inputs, creature.state)
            creature.tumble_probability = outputs[TUMBLE]
            creature.deposit = outputs[DEPOSIT]
            creature.decisions += 1
            if creature.rng.random() < creature.tumble_probability:
                creature.action = 'tumble'
                creature.turn_left = creature.rng.uniform(settings['tumble_min'], settings['tumble_max'])
                creature.turn_rate = creature.rng.uniform(-math.pi, math.pi) / creature.turn_left
                creature.tumbles += 1
            else:
                creature.action = 'run'
            creature.decision_left = settings['decision_seconds']

        if creature.turn_left > 0.0:
            creature.turn_left -= dt
            mode, run_speed, turn = 'tumble', 0.0, creature.turn_rate
        else:
            creature.action = 'run'
            creature.decision_left -= dt
            mode, run_speed, turn = 'run', 1.0, 0.0
        creature.prev_tumble = 1.0 if mode == 'tumble' else 0.0

        # Energy. Eating restores, moving and simply existing cost.
        if eating:
            gain = min(settings['food_per_s'] * dt, settings['max_energy'] - creature.energy)
            creature.energy += gain
            creature.eaten += gain
            creature.eating_seconds += dt
        # Cost the speed the Body actually reached, not the one we asked for,
        # so an individual pinned against a wall does not pay to go nowhere.
        # core.World bills the same way, as self.speed / c['speed'].
        if settings['run_speed'] > 0.0:
            motion = min(1.0, abs(speed) / settings['run_speed'])
        else:
            motion = 1.0 if mode == 'run' else 0.0
        creature.energy -= dt * (settings['basal_cost'] + settings['motion_cost'] * motion)
        if creature.energy <= 0.0:
            creature.energy = 0.0
            creature.starved = True
            mode, run_speed, turn = 'idle', 0.0, 0.0

        # Write to the field. The path trace is always laid down; the food
        # trace only where prey actually is. Being shot has its own channel
        # now, so a place that hurt is no longer confused with a meal.
        if not creature.starved:
            self.field.deposit(x, z, PATH, settings['deposit_path'] * dt)
            if eating:
                amount = settings['deposit_food'] * creature.deposit * dt
                self.field.deposit(x, z, FOOD, amount)

        return dict(id=creature.id, mode=mode, speed=run_speed, turn=turn,
                    deposit=creature.deposit,
                    energy=creature.energy / settings['max_energy'],
                    starved=creature.starved)

    # -- reporting ------------------------------------------------------

    def summary(self):
        return dict(tick=self.tick, time_s=self.time, alive=len(self.creatures),
                    deaths_recorded=self.deaths,
                    species=self.brain.species, generation=self.brain.generation,
                    fingerprint=self.brain.fingerprint,
                    training_enabled=False, training_steps=0,
                    creatures=[dict(id=c.id, energy=c.energy, decisions=c.decisions,
                                    tumbles=c.tumbles, eaten=c.eaten,
                                    alive_s=c.alive_seconds, eating_s=c.eating_seconds,
                                    starved=c.starved)
                               for c in self.creatures.values()])
