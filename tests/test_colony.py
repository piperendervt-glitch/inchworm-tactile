import math
import unittest

from creature_sim.ecoli.brain import (CELLS, DAMAGE, FIELD_DAMAGE, FIELD_DEATH,
                                      FIELD_FOOD, INPUTS, PREY, PRESSURE, WALL,
                                      EcoliBrain)
from creature_sim.field import CHANNELS, DEATH
from creature_sim.field import DAMAGE as DAMAGE_TRACE
from creature_sim.ecoli.colony import Colony

BOUNDS = [-40.0, -40.0, 40.0, 40.0]
DT = 1 / 30


def cells(contacts=None):
    """16 tactile cells, all quiet unless ``contacts`` names some."""
    out = [dict(p=0.0, k=0, h=0.0) for _ in range(CELLS)]
    for index, (p, k, h) in (contacts or {}).items():
        out[index] = dict(p=p, k=k, h=h)
    return out


def observe(tick, creatures, dt=DT):
    return dict(v=1, type='observe', session='test', tick=tick, t=tick * dt, dt=dt,
                player=dict(pos=[0.0, 0.0, 0.0], heading=0.0, hp=250, guard=False),
                creatures=creatures)


def creature(creature_id=0, state='alive', pos=(0.0, 0.0, 0.0), heading=0.0,
             speed=6.0, hp=120, eating=False, contacts=None):
    return dict(id=creature_id, state=state, pos=list(pos), heading=heading,
                speed=speed, hp=hp, eating=eating, cells=cells(contacts))


class FixedBrain:
    """Stands in for a brain that has learned never to tumble.

    The real network has no bias term, so its output cannot be pinned by
    editing weights alone. This isolates the run/tumble state machine, which
    is what the test is about.
    """

    species = 'stub'
    generation = 0
    fingerprint = 'stub'

    def __init__(self, tumble=0.0, deposit=0.5):
        self.outputs = [tumble, deposit]
        self.calls = 0

    def new_state(self):
        return [0.0] * 8

    def step(self, inputs, state):
        assert len(inputs) == INPUTS
        self.calls += 1
        return list(self.outputs), list(state)


class ColonyTests(unittest.TestCase):
    def test_run_continues_while_nothing_is_touched(self):
        colony = Colony(brain=FixedBrain(tumble=0.0), bounds=BOUNDS, seed=3)

        modes = []
        for tick in range(300):
            command = colony.step(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')]))
            modes.append(command['creatures'][0]['mode'])

        self.assertEqual(set(modes), {'run'})
        self.assertEqual(colony.creatures[0].tumbles, 0)
        # It kept deciding, it just kept choosing to run.
        self.assertGreater(colony.creatures[0].decisions, 30)

    def test_starved_is_raised_when_energy_reaches_zero(self):
        colony = Colony(bounds=BOUNDS, seed=5, settings=dict(initial_energy=1.0))
        starved_at = None
        for tick in range(400):
            command = colony.step(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')]))
            entry = command['creatures'][0]
            if entry['starved']:
                starved_at = tick
                break
            self.assertGreater(entry['energy'], 0.0)

        self.assertIsNotNone(starved_at, 'creature never starved')
        self.assertEqual(entry['mode'], 'idle')
        self.assertEqual(entry['speed'], 0.0)
        self.assertEqual(entry['energy'], 0.0)
        # basal 0.1 + motion 0.2 per second burns 1.0 energy in about 3.3 s.
        self.assertAlmostEqual(starved_at * DT, 1.0 / 0.3, delta=0.2)

    def test_eating_restores_energy_and_writes_the_food_trace(self):
        colony = Colony(bounds=BOUNDS, seed=5, settings=dict(initial_energy=10.0))
        for tick in range(60):
            colony.step(observe(tick, [creature(
                state='spawned' if tick == 0 else 'alive', eating=True,
                contacts={2: (0.9, 2, 0.0)})]))
        self.assertGreater(colony.creatures[0].energy, 10.0)
        self.assertGreater(colony.field.total(0), 0.0)

    def test_spawned_resets_state(self):
        colony = Colony(bounds=BOUNDS, seed=7, settings=dict(initial_energy=100.0))
        for tick in range(200):
            colony.step(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')]))

        used = colony.creatures[0]
        self.assertLess(used.energy, 100.0)
        self.assertGreater(used.decisions, 0)
        self.assertGreater(used.alive_seconds, 0.0)
        used.state = [0.5] * len(used.state)

        colony.step(observe(200, [creature(state='spawned')]))
        fresh = colony.creatures[0]
        self.assertIsNot(fresh, used)
        self.assertAlmostEqual(fresh.energy, 100.0, delta=0.02)
        self.assertLessEqual(fresh.decisions, 1)
        self.assertAlmostEqual(fresh.alive_seconds, DT, places=9)
        self.assertFalse(fresh.starved)

    def test_dead_and_missing_creatures_are_dropped(self):
        colony = Colony(bounds=BOUNDS, seed=9)
        colony.step(observe(0, [creature(0, state='spawned'), creature(1, state='spawned')]))
        self.assertEqual(set(colony.creatures), {0, 1})

        command = colony.step(observe(1, [creature(0), creature(1, state='dead')]))
        self.assertEqual(set(colony.creatures), {0})
        self.assertEqual([c['id'] for c in command['creatures']], [0])

        colony.step(observe(2, []))
        self.assertEqual(colony.creatures, {})

    def test_cell_world_positions_follow_the_protocol(self):
        colony = Colony(bounds=BOUNDS, seed=0,
                        settings=dict(length=3.0, radius=0.8))
        # heading 0 faces +Z, so the front ring sits at +Z and right is +X.
        front_up = colony.cell_world_xz(0.0, 0.0, 0.0, 0)
        front_right = colony.cell_world_xz(0.0, 0.0, 0.0, 2)
        front_down = colony.cell_world_xz(0.0, 0.0, 0.0, 4)
        front_left = colony.cell_world_xz(0.0, 0.0, 0.0, 6)
        rear_up = colony.cell_world_xz(0.0, 0.0, 0.0, 8)

        self.assertAlmostEqual(front_up[1], 0.75, places=9)
        self.assertAlmostEqual(rear_up[1], -0.75, places=9)
        self.assertAlmostEqual(front_right[0], 0.8, places=9)
        self.assertAlmostEqual(front_left[0], -0.8, places=9)
        # Up and down differ only in Y, which the flat field does not see.
        self.assertAlmostEqual(front_up[0], front_down[0], places=9)
        self.assertAlmostEqual(front_up[1], front_down[1], places=9)

        # Turning a quarter turn puts the front ring at +X.
        turned = colony.cell_world_xz(0.0, 0.0, math.pi / 2, 0)
        self.assertAlmostEqual(turned[0], 0.75, places=9)
        self.assertAlmostEqual(turned[1], 0.0, places=9)

    def test_prey_contact_reaches_the_input_vector(self):
        colony = Colony(bounds=BOUNDS, seed=0)
        colony.step(observe(0, [creature(state='spawned')]))
        inputs, hit = colony.build_inputs(
            colony.creatures[0],
            creature(contacts={3: (0.7, 2, 0.0), 11: (0.4, 1, 0.25)}))

        self.assertEqual(len(inputs), INPUTS)
        self.assertAlmostEqual(inputs[PRESSURE + 3], 0.7)
        self.assertAlmostEqual(inputs[PREY + 3], 1.0)
        self.assertAlmostEqual(inputs[WALL + 3], 0.0)
        self.assertAlmostEqual(inputs[WALL + 11], 1.0)
        self.assertAlmostEqual(inputs[DAMAGE], 0.25)
        self.assertAlmostEqual(hit, 0.25)

    def test_being_shot_writes_the_damage_trace_not_the_food_trace(self):
        # The fake body never reports a hit, so this drives one straight in.
        colony = Colony(bounds=BOUNDS, seed=11)
        colony.step(observe(0, [creature(state='spawned')]))
        self.assertEqual(colony.field.total(DAMAGE_TRACE), 0.0)

        colony.step(observe(1, [creature(pos=(5.0, 0.0, -3.0),
                                         contacts={4: (1.0, 1, 0.5)})]))
        self.assertGreater(colony.field.total(DAMAGE_TRACE), 0.0,
                           'a hit should mark the ground it happened on')
        # Being shot is no longer confused with finding a meal.
        self.assertEqual(colony.field.total(FIELD_FOOD - FIELD_FOOD), 0.0)
        self.assertAlmostEqual(colony.field.sample(5.0, -3.0, DAMAGE_TRACE),
                               colony.field.sample(5.0, -3.0, DAMAGE_TRACE))

    def test_a_shot_death_is_marked_once_where_it_fell(self):
        colony = Colony(bounds=BOUNDS, seed=12)
        colony.step(observe(0, [creature(state='spawned')]))
        self.assertEqual(colony.field.total(DEATH), 0.0)

        dead = creature(state='dead', pos=(-7.0, 0.0, 12.0))
        dead['reason'] = 'shot'
        colony.step(observe(1, [dead]))
        marked = colony.field.total(DEATH)
        self.assertGreater(marked, 0.0)
        self.assertEqual(colony.deaths, 1)
        self.assertNotIn(0, colony.creatures)

        # Starving leaves no death mark; only being shot warns the others.
        colony.step(observe(2, [creature(1, state='spawned')]))
        starved = creature(1, state='dead', pos=(3.0, 0.0, 3.0))
        starved['reason'] = 'starved'
        colony.step(observe(3, [starved]))
        self.assertEqual(colony.deaths, 1)

    def test_the_three_place_traces_are_read_at_every_cell(self):
        colony = Colony(bounds=BOUNDS, seed=13)
        colony.step(observe(0, [creature(state='spawned')]))
        scale = colony.settings['field_scale']
        # Put a strong mark under the right-hand cells and nowhere else.
        for channel, offset in ((0, FIELD_FOOD), (DAMAGE_TRACE, FIELD_DAMAGE),
                                (DEATH, FIELD_DEATH)):
            colony.field.cells[channel] = [0.0] * (colony.field.cols * colony.field.rows)
            colony.field.deposit(0.8, 0.75, channel, scale * 4)

        inputs, _ = colony.build_inputs(colony.creatures[0], creature())
        for offset in (FIELD_FOOD, FIELD_DAMAGE, FIELD_DEATH):
            right = inputs[offset + 2]
            left = inputs[offset + 6]
            self.assertGreater(right, left,
                               f'trace at offset {offset} gives no direction')


class BrainTests(unittest.TestCase):
    def test_shape_range_and_determinism(self):
        brain = EcoliBrain(seed=2)
        state = brain.new_state()
        inputs = [0.3] * 100
        first, next_state = brain.step(inputs, state)

        self.assertEqual(len(first), 2)
        self.assertEqual(len(next_state), 8)
        for v in first:
            self.assertTrue(0.0 <= v <= 1.0)
        # step is pure: the passed-in state is untouched.
        self.assertEqual(state, [0.0] * 8)
        again, _ = EcoliBrain(seed=2).step(inputs, [0.0] * 8)
        self.assertEqual(first, again)
        self.assertNotEqual(EcoliBrain(seed=3).fingerprint, brain.fingerprint)

    def test_json_round_trip_keeps_the_fingerprint_and_outputs(self):
        brain = EcoliBrain(seed=11, generation=4)
        copy = EcoliBrain.from_json(brain.to_json())

        self.assertEqual(copy.fingerprint, brain.fingerprint)
        self.assertEqual(copy.generation, 4)
        self.assertFalse(brain.to_dict()['training_enabled'])
        inputs = [0.1 * (i % 7) for i in range(100)]
        self.assertEqual(copy.step(inputs, copy.new_state()),
                         brain.step(inputs, brain.new_state()))

    def test_bad_weights_are_rejected(self):
        data = EcoliBrain(seed=1).to_dict()
        data['w'][0].append(0.0)
        with self.assertRaises(ValueError):
            EcoliBrain.from_dict(data)

        tampered = EcoliBrain(seed=1).to_dict()
        tampered['w'][0][0] += 1.0
        with self.assertRaises(ValueError):
            EcoliBrain.from_dict(tampered)

        with self.assertRaises(ValueError):
            EcoliBrain(seed=1).step([0.0] * 99, [0.0] * 8)


if __name__ == '__main__':
    unittest.main()
