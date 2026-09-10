"""Genes, energy, division and the jellies' nerve ring."""

import math
import random
import unittest

from creature_sim import genome as genes
from creature_sim.ecoli.brain import EARS, INPUTS
from creature_sim.ecoli.colony import Colony
from creature_sim.field import PLANKTON, StigmergyField
from creature_sim.jelly.colony import RING, SKIN_TO_RING, JellyColony
from creature_sim.physiology import DEFAULTS as PHYSIOLOGY, Physiology

BOUNDS = [-40.0, -40.0, 40.0, 40.0]
HZ = 30


def quiet_cells():
    return [dict(p=0.0, k=0, h=0.0) for _ in range(16)]


def creature(cid=0, species='ecoli', **kw):
    out = dict(id=cid, species=species, state='alive', pos=[0.0, 0.0, 0.0], heading=0.0,
               speed=0.0, hp=120, eating=False, cells=quiet_cells())
    out.update(kw)
    return out


def observe(tick, creatures, **kw):
    out = dict(v=1, type='observe', session='s', tick=tick, t=tick / HZ, dt=1 / HZ,
               creatures=creatures)
    out.update(kw)
    return out


class GenomeTests(unittest.TestCase):
    def test_children_stay_in_range_and_differ_from_the_parent(self):
        rng = random.Random(1)
        parent = genes.founder('ecoli', rng)
        for key in parent:
            low, high, _ = genes.RANGES[key]
            self.assertTrue(low <= parent[key] <= high, key)
        children = [genes.mutate(parent, rng) for _ in range(50)]
        for child in children:
            for key in child:
                low, high, _ = genes.RANGES[key]
                self.assertTrue(low <= child[key] <= high, key)
        speeds = [c['speed'] for c in children]
        self.assertGreater(max(speeds) - min(speeds), 0.05, 'mutation should spread the children')
        # Small steps: most children are near the parent.
        near = sum(1 for c in children if abs(c['speed'] / parent['speed'] - 1) < 0.25)
        self.assertGreater(near, 35)

    def test_the_same_seed_gives_the_same_founder(self):
        self.assertEqual(genes.founder('jelly', random.Random(7)), genes.founder('jelly', random.Random(7)))
        self.assertIn('pace', genes.founder('jelly', random.Random(7)))
        self.assertNotIn('pace', genes.founder('ecoli', random.Random(7)))


class PhysiologyTests(unittest.TestCase):
    def test_a_full_individual_running_and_finding_nothing_starves_within_two_minutes(self):
        body = Physiology(100.0)
        t = 0.0
        while not body.starved and t < 300:
            body.step(1 / HZ, PHYSIOLOGY, 1.0, 1.0, 0.85)     # running flat out
            t += 1 / HZ
        self.assertTrue(body.starved)
        self.assertLess(t, 120.0, f'starved only after {t:.0f} s')
        self.assertGreater(t, 60.0, 'but with time to look for food')

    def test_held_above_satiety_it_asks_to_divide_once_then_waits(self):
        body = Physiology(100.0)
        asks = 0
        for _ in range(int(3.0 * HZ)):
            body.feed(10.0, 100.0)               # kept full
            if body.step(1 / HZ, PHYSIOLOGY, 1.0, 0.0, 0.85):
                asks += 1
        self.assertEqual(asks, 1, 'one ask, then the cooldown')
        share = body.split(PHYSIOLOGY)
        self.assertAlmostEqual(share, 50.0, delta=0.5)
        self.assertAlmostEqual(body.energy, 50.0, delta=0.5)
        self.assertEqual(body.divisions, 1)

    def test_below_satiety_it_never_asks(self):
        body = Physiology(60.0)
        for _ in range(int(3.0 * HZ)):
            body.feed(10.0, 100.0)
            body.energy = min(body.energy, 70.0)
            self.assertFalse(body.step(1 / HZ, PHYSIOLOGY, 1.0, 0.0, 0.85))


class EcoliLineageTests(unittest.TestCase):
    def test_a_bite_of_mech_is_a_meal_and_a_wreck_is_a_snack(self):
        colony = Colony(bounds=BOUNDS, seed=3, settings=dict(initial_energy=20.0))
        colony.step(observe(0, [creature(state='spawned')]))
        colony.step(observe(1, [creature(bite='mech')]))
        after_mech = colony.creatures[0].energy
        self.assertGreater(after_mech, 20.0 + 50.0)
        colony = Colony(bounds=BOUNDS, seed=3, settings=dict(initial_energy=20.0))
        colony.step(observe(0, [creature(state='spawned')]))
        colony.step(observe(1, [creature(bite='wreck')]))
        self.assertLess(colony.creatures[0].energy, 20.0 + 10.0)

    def test_a_child_inherits_and_the_parent_pays(self):
        colony = Colony(bounds=BOUNDS, seed=3, settings=dict(initial_energy=100.0))
        colony.step(observe(0, [creature(state='spawned')]))
        parent = colony.creatures[0]
        # Held full until it asks.
        asked = None
        for tick in range(1, 200):
            parent.body.energy = 100.0
            command = colony.step(observe(tick, [creature()]))
            if command['creatures'][0]['divide']:
                asked = tick
                break
        self.assertIsNotNone(asked, 'a full E. coli should ask to divide')
        parent.body.energy = 100.0
        colony.step(observe(asked + 1, [creature(), creature(cid=1, state='spawned', parent=0)]))
        child = colony.creatures[1]
        self.assertEqual(colony.births, 1)
        self.assertAlmostEqual(parent.energy + child.energy, 100.0, delta=1.0)
        self.assertLess(parent.energy, 60.0)
        for key in parent.genome:
            low, high, kind = genes.RANGES[key]
            if kind == 'log':
                self.assertLess(abs(child.genome[key] / parent.genome[key] - 1), 0.6, key)
            else:
                self.assertLess(abs(child.genome[key] - parent.genome[key]), (high - low) * 0.25, key)
        self.assertNotEqual(child.genome, parent.genome)

    def test_the_speed_gene_scales_the_run(self):
        colony = Colony(bounds=BOUNDS, seed=3)
        colony.step(observe(0, [creature(state='spawned')]))
        colony.creatures[0].genome['speed'] = 0.5
        for tick in range(1, 40):
            entry = colony.step(observe(tick, [creature()]))['creatures'][0]
            if entry['mode'] == 'run':
                self.assertAlmostEqual(entry['speed'], 0.5)
                return
        self.fail('never ran')


class ReflexPolicyTests(unittest.TestCase):
    def colony(self, **genome):
        colony = Colony(bounds=BOUNDS, seed=3, policy='genes')
        colony.step(observe(0, [creature(state='spawned')]))
        c = colony.creatures[0]
        c.genome.update(dict(base=0.3, hearing=0.0, food=0.0, fear=0.0, touch=0.0, noise=0.0))
        c.genome.update(genome)
        return colony, c

    def test_worked_examples(self):
        colony, c = self.colony()
        quiet = [0.0] * INPUTS
        self.assertAlmostEqual(Colony.reflex(c, quiet), 0.3, places=3)
        loud_ahead = list(quiet)
        loud_ahead[EARS] = 0.5
        c.genome['hearing'] = 2.0
        self.assertAlmostEqual(Colony.reflex(c, loud_ahead), 0.537, places=2)
        c.genome['hearing'] = -2.0
        self.assertAlmostEqual(Colony.reflex(c, loud_ahead), 0.136, places=2)

    def test_the_floor_is_not_a_wall_ahead_but_plankton_is_a_smell(self):
        colony, c = self.colony(touch=3.0)
        floored = [0.0] * INPUTS
        for i in (3, 4, 5, 11, 12, 13):             # the cells resting on the floor
            floored[i] = 0.8
            floored[16 + i] = 1.0                   # WALL offset is 16
        self.assertAlmostEqual(Colony.reflex(c, floored), 0.3, places=3, msg='the floor must not count')
        floored[1] = 0.8; floored[16 + 1] = 1.0     # a real wall on the front-right
        self.assertGreater(Colony.reflex(c, floored), 0.8)

        c.genome.update(touch=0.0, food=-3.0)
        quiet = [0.0] * INPUTS
        self.assertLess(Colony.reflex(c, quiet, scent=(0.5, 0.0)), 0.1, 'smell ahead: keep running')
        self.assertGreater(Colony.reflex(c, quiet, scent=(0.0, 0.5)), 0.6, 'smell behind: turn')
        # And the scent really is read from the plankton channel around the skin.
        colony.field.deposit(3.0, 0.0, PLANKTON, 8.0)
        front, back = colony.scent(creature(pos=[1.0, 0.0, 0.0], heading=math.pi / 2))   # facing +X, toward it
        self.assertGreater(front, back)

    def test_wrecks_and_jellies_smell_of_food(self):
        colony = Colony(bounds=BOUNDS, seed=1)
        wreck = dict(kind='wreck', id=0, pos=[10.0, 0.0, 10.0], hp=300, maxHp=300)
        for tick in range(30):
            colony.step(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')],
                                prey=[wreck], jellies=[[-10.0, 0.0, -10.0]]))
        from creature_sim.field import FOOD
        # One second of shedding, spread over the nearest cells and diffusing.
        self.assertGreater(colony.field.sample(10.0, 10.0, FOOD), 0.3, 'a wreck smells strongly')
        self.assertGreater(colony.field.sample(-10.0, -10.0, FOOD), 0.05, 'a jelly smells faintly')
        self.assertGreater(colony.field.sample(10.0, 10.0, FOOD), colony.field.sample(-10.0, -10.0, FOOD))
        self.assertLess(colony.field.sample(0.0, 30.0, FOOD), 0.05, 'nothing where nothing is')

    def test_the_net_is_never_asked(self):
        colony, c = self.colony(noise=1.0)
        before = list(c.state)
        for tick in range(1, 60):
            colony.step(observe(tick, [creature()]))
        self.assertEqual(c.state, before, 'recurrent state untouched: the brain was not run')
        self.assertGreater(c.decisions, 5)

    def test_founders_differ_from_one_another(self):
        colony = Colony(bounds=BOUNDS, seed=9, policy='genes')
        colony.step(observe(0, [creature(cid=i, state='spawned') for i in range(12)]))
        hearing = [c.genome['hearing'] for c in colony.creatures.values()]
        self.assertGreater(max(hearing) - min(hearing), 0.5, hearing)
        self.assertTrue(any(h > 0 for h in hearing) and any(h < 0 for h in hearing),
                        'both seekers and fleers should be born')

    def test_an_old_lineage_entry_is_completed(self):
        old = dict(speed=0.9, metab=0.6, satiety=0.8, tumble=1.2)
        colony = Colony(bounds=BOUNDS, seed=1, policy='genes', founders=[dict(genome=old)])
        colony.step(observe(0, [creature(state='spawned')]))
        g = colony.creatures[0].genome
        for key in ('base', 'hearing', 'food', 'fear', 'touch', 'noise'):
            self.assertIn(key, g)
        self.assertLess(abs(g['speed'] - 0.9), 0.2)


class JellyRingTests(unittest.TestCase):
    def test_skin_cells_map_to_the_swimming_plane(self):
        # Front segment, ring 2 (right side) -> front-right; rear segment ring 6 (left) -> back-left.
        self.assertEqual(SKIN_TO_RING[0], 0)       # front, up
        self.assertEqual(SKIN_TO_RING[2], 1)       # front, right
        self.assertEqual(SKIN_TO_RING[8], 4)       # rear, up -> straight back
        self.assertEqual(SKIN_TO_RING[8 + 6], 5)   # rear, left -> back-left
        self.assertEqual(SKIN_TO_RING[6], 7)       # front, left

    def test_a_poke_sends_waves_both_ways_that_meet_opposite(self):
        colony = JellyColony(bounds=BOUNDS, seed=1)
        colony.step(observe(0, [creature(species='jelly', state='spawned')]))
        j = colony.jellies[0]
        j.timers = []                               # no pacemakers for this test
        colony.stimulate(j, 0)
        fired_at = {}
        for step in range(1, 12):
            for cell, _ in colony.ring_step(j):
                fired_at.setdefault(cell, step)
        self.assertEqual(fired_at.get(1), 1)
        self.assertEqual(fired_at.get(7), 1)
        self.assertEqual(fired_at.get(4), 4, 'the two waves meet at the far side')

    def test_the_floor_does_not_poke(self):
        colony = JellyColony(bounds=BOUNDS, seed=1)
        colony.step(observe(0, [creature(species='jelly', state='spawned')]))
        j = colony.jellies[0]
        j.timers = []
        cells = quiet_cells()
        for i in (3, 4, 5, 11, 12, 13):
            cells[i] = dict(p=0.9, k=1, h=0.0)
        for tick in range(1, 30):
            colony.step(observe(tick, [creature(species='jelly', cells=cells)]))
        self.assertEqual(j.pokes, 0, 'resting on the floor is not a poke')

    def test_a_poke_from_the_right_pushes_left(self):
        colony = JellyColony(bounds=BOUNDS, seed=1)
        colony.step(observe(0, [creature(species='jelly', state='spawned')]))
        j = colony.jellies[0]
        j.timers = []
        cells = quiet_cells()
        cells[2] = dict(p=0.9, k=1, h=0.0)          # front segment, right side
        for tick in range(1, 40):
            entry = colony.step(observe(tick, [creature(species='jelly', heading=0.0, cells=cells if tick == 1 else quiet_cells())]))[0]
        self.assertLess(j.vx, -0.05, f'should be pushed to -X (left): vx {j.vx:.3f}')
        self.assertEqual(entry['mode'], 'run')
        self.assertLess(entry['turn'], 0.0, 'and asked to turn left toward that drift')

    def test_a_pacemaker_keeps_it_swimming(self):
        colony = JellyColony(bounds=BOUNDS, seed=2)
        colony.step(observe(0, [creature(species='jelly', state='spawned')]))
        j = colony.jellies[0]
        j.timers = [[4, 1]]                         # at the back: swims forward
        j.genome['pace'] = 30
        running = 0
        for tick in range(1, 300):
            entry = colony.step(observe(tick, [creature(species='jelly')]))[0]
            running += entry['mode'] == 'run'
        self.assertGreater(running, 200)
        self.assertGreater(j.vz, 0.0, 'pacemaker at the back pushes forward (+Z)')
        self.assertGreater(j.fired, RING)

    def test_it_grazes_plankton_that_wrecks_shed_and_eats_nothing_else(self):
        field = StigmergyField(BOUNDS)
        colony = JellyColony(field=field, seed=2, settings=dict(initial_energy=30.0))
        wreck = dict(kind='wreck', id=0, pos=[0.0, 0.0, 0.0], hp=300, maxHp=300)
        colony.step(observe(0, [creature(species='jelly', state='spawned')], prey=[wreck]))
        for tick in range(1, 20 * HZ):
            colony.step(observe(tick, [creature(species='jelly')], prey=[wreck]))
            field.update(1 / HZ)
        j = colony.jellies[0]
        self.assertGreater(field.total(PLANKTON), 0.0)
        self.assertGreater(j.grazed, 0.0)
        # Touching a mech means nothing to a jelly: no bite, no energy from it.
        before = j.energy
        colony.step(observe(20 * HZ, [creature(species='jelly', eating=True, bite='mech')], prey=[]))
        self.assertLessEqual(j.energy, before + 1.0)

    def test_a_jelly_starves_quickly_with_nothing_to_graze(self):
        colony = JellyColony(bounds=BOUNDS, seed=2)
        starved_at = None
        for tick in range(0, 120 * HZ):
            entry = colony.step(observe(tick, [creature(species='jelly', state='spawned' if tick == 0 else 'alive')]))[0]
            if entry['starved']:
                starved_at = tick / HZ
                break
        self.assertIsNotNone(starved_at)
        self.assertLess(starved_at, 60.0)

    def test_a_jelly_child_inherits_pacemakers(self):
        colony = JellyColony(bounds=BOUNDS, seed=2, settings=dict(initial_energy=100.0))
        colony.step(observe(0, [creature(species='jelly', state='spawned')]))
        parent = colony.jellies[0]
        parent.timers = [[3, 5]]
        parent.body.energy = 100.0
        colony.step(observe(1, [creature(species='jelly'), creature(cid=1, species='jelly', state='spawned', parent=0)]))
        child = colony.jellies[1]
        self.assertEqual(len(child.timers), 1)
        self.assertIn(child.timers[0][0], (2, 3, 4))
        self.assertEqual(colony.births, 1)
        self.assertLess(parent.energy, 60.0)

    def test_ecoli_entries_are_left_to_the_other_colony(self):
        colony = JellyColony(bounds=BOUNDS, seed=2)
        out = colony.step(observe(0, [creature(state='spawned'), creature(cid=1, species='jelly', state='spawned')]))
        self.assertEqual([e['id'] for e in out], [1])


if __name__ == '__main__':
    unittest.main()
