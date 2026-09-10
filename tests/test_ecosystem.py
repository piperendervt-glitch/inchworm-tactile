"""The selection-only simulator, and the lineage it leaves behind."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim import current, lineage
from creature_sim.ecoli.brain import EcoliBrain
from creature_sim.ecoli.colony import Colony
from creature_sim.ecosystem import Ecosystem
from creature_sim.jelly.colony import JellyColony
from creature_sim.logs import Session
from creature_sim.viewer import LogSource
from tests.test_lineage import BOUNDS, creature, observe
from tests.test_replay import write_session


class LineageFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / 'lineage.json'

    def test_the_longest_lived_survivors_are_kept_and_a_wipe_out_keeps_the_old_line(self):
        colony = Colony(bounds=BOUNDS, seed=1)
        jellies = JellyColony(bounds=BOUNDS, seed=1)
        colony.step(observe(0, [creature(cid=i, state='spawned') for i in range(12)]))
        jellies.step(observe(0, [creature(cid=20 + i, species='jelly', state='spawned') for i in range(3)]))
        for i, c in enumerate(colony.creatures.values()):
            c.alive_seconds = float(i)
        data = lineage.save(self.path, colony, jellies)
        self.assertEqual(len(data['ecoli']), lineage.KEEP)
        self.assertEqual(data['ecoli'][0]['alive_s'], 11.0, 'longest lived first')
        self.assertEqual(len(data['jelly']), 3)
        self.assertIn('pacemakers', data['jelly'][0])

        empty = JellyColony(bounds=BOUNDS, seed=2)
        again = lineage.save(self.path, colony, empty)
        self.assertEqual(len(again['jelly']), 3, 'no jelly survived, so the old line stays')
        self.assertEqual(lineage.load(self.path)['jelly'], again['jelly'])

    def test_an_extinction_clears_the_file(self):
        colony = Colony(bounds=BOUNDS, seed=1)
        colony.step(observe(0, [creature(state='spawned')]))
        lineage.save(self.path, colony, None)
        self.assertEqual(len(lineage.load(self.path)['ecoli']), 1)
        lineage.clear(self.path, session='s')
        data = lineage.load(self.path)
        self.assertEqual(data['ecoli'], [])
        self.assertEqual(data['jelly'], [])
        self.assertEqual(data['extinct'], 's')

    def test_the_server_drops_the_lineage_on_an_extinct_bye_and_thins_its_log(self):
        from creature_sim.server import BrainServer
        server = BrainServer(sessions_dir=self.tmp / 'sessions', quiet=True,
                             lineage_path=self.path, log_every=10)
        hello = dict(v=1, type='hello', session='x', tick=0, body={}, arena=dict(bounds=BOUNDS))
        server.handle(hello)
        for tick in range(30):
            server.handle(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')], session='x'))
        self.assertEqual(server.log.ticks, 3, 'one tick in ten is logged')
        server.handle(dict(v=1, type='bye', session='x', tick=30, reason='extinct'))
        self.assertEqual(lineage.load(self.path)['ecoli'], [])
        self.assertIn('extinct', lineage.load(self.path))

    def test_the_server_can_keep_no_ticks_and_still_save_the_lineage(self):
        from creature_sim.server import BrainServer
        server = BrainServer(sessions_dir=self.tmp / 'sessions', quiet=True,
                             lineage_path=self.path, log_every=0)
        server.handle(dict(v=1, type='hello', session='y', tick=0, body={}, arena=dict(bounds=BOUNDS)))
        for tick in range(20):
            server.handle(observe(tick, [creature(state='spawned' if tick == 0 else 'alive')], session='y'))
        folder = server.log.directory
        server.handle(dict(v=1, type='bye', session='y', tick=20, reason='mission_end'))
        self.assertEqual((folder / 'link.jsonl').read_text(encoding='utf-8'), '', 'no ticks kept')
        self.assertTrue((folder / 'summary.json').exists())
        self.assertEqual(len(lineage.load(self.path)['ecoli']), 1)

    def test_founders_come_from_the_file_and_are_mutated_once(self):
        saved = dict(ecoli=[dict(genome=dict(speed=0.5, metab=1.5, satiety=0.9, tumble=2.0))],
                     jelly=[dict(genome=dict(speed=0.5, metab=1.5, satiety=0.9, pace=60.0, suck=1.5, noci=0.5),
                                 pacemakers=[6])])
        colony = Colony(bounds=BOUNDS, seed=3, founders=saved['ecoli'])
        colony.step(observe(0, [creature(cid=i, state='spawned') for i in range(4)]))
        for c in colony.creatures.values():
            self.assertLess(abs(c.genome['speed'] - 0.5), 0.2, c.genome)
            self.assertGreater(c.genome['tumble'], 1.2)
        jellies = JellyColony(bounds=BOUNDS, seed=3, founders=saved['jelly'])
        jellies.step(observe(0, [creature(cid=16, species='jelly', state='spawned')]))
        j = jellies.jellies[16]
        self.assertEqual([c for c, _ in j.timers], [6])
        self.assertGreater(j.genome['pace'], 45.0)


class EcosystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.session = write_session(self.tmp / 'session', seconds=3.0,
                                     walk=lambda t: (-30.0 + (t * 14.0) % 60.0, 0.0),
                                     bounds=[-40, -40, 40, 40])

    def test_both_species_live_eat_divide_and_die_and_the_run_can_be_watched(self):
        world = Ecosystem(self.session, brain=EcoliBrain(seed=1), seed=1, ecoli=6, jelly=6,
                          ecoli_capacity=10, jelly_capacity=10)
        # A wreck the whole time, so plankton grows and jellies have something.
        world.session._cached_tracks = (world.track, [(0.0, [dict(kind='wreck', id=0, pos=[0.0, 0.0, 0.0],
                                                                    hp=300, maxHp=300)], [])])
        world.world = world.session.world_track()
        # Push the jellies onto the mound and the E. coli onto the jellies.
        for b in world.bodies:
            if b.species == 'jelly':
                b.x, b.z = 0.5, 0.5
            else:
                b.x, b.z = 0.0, -1.5
                b.heading = 0.0
        result = world.run(40.0)
        self.assertEqual(result['ticks'], 1200)
        self.assertGreater(result['plankton'], 0.0)
        self.assertGreater(result['bites'].get('jelly', 0), 0, 'E. coli should bite jellies')
        self.assertGreater(result['deaths']['jelly']['eaten'], 0)
        total_deaths = sum(sum(d.values()) for d in result['deaths'].values())
        self.assertGreater(total_deaths + sum(result['births'].values()), 0)
        self.assertLessEqual(len(world.alive('ecoli')), 10)
        self.assertLessEqual(len(world.alive('jelly')), 10)

        out = world.write(self.tmp / 'out', self.tmp / 'lineage.json')
        rows = [json.loads(l) for l in (out / 'link.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1200)
        species = {c['species'] for c in rows[0]['observe']['creatures']}
        self.assertEqual(species, {'ecoli', 'jelly'})
        self.assertTrue(any('ring' in c for c in rows[5]['command']['creatures']), 'jelly rings ride along')
        self.assertTrue(any(c.get('bite') == 'jelly' for r in rows for c in r['observe']['creatures']))
        self.assertTrue(any(c['state'] == 'dead' and c.get('reason') == 'eaten'
                            for r in rows for c in r['observe']['creatures']))
        saved = json.loads((self.tmp / 'lineage.json').read_text(encoding='utf-8'))
        self.assertGreater(len(saved['ecoli']), 0)
        # The viewer reads it like any session.
        source = LogSource(out)
        self.assertEqual(len(source), 1200)
        source.close()
        self.assertEqual(Session(out).hello['pilot']['kind'], 'ecosystem')

    def test_a_session_recorded_with_a_ghost_pilot_still_runs(self):
        from creature_sim.server import SessionLog
        from tests.test_replay import sample
        log = SessionLog(self.tmp / 'ghost')
        log.meta(sample('hello'), sample('welcome'))
        wreck = dict(kind='wreck', id=0, pos=[3.0, 0.0, 3.0], hp=300, maxHp=300)
        for tick in range(60):
            log.write(dict(v=1, type='observe', session='s', tick=tick, t=tick / 30, dt=1 / 30,
                           creatures=[], prey=[wreck], sounds=[]),
                      dict(v=1, type='command', session='s', tick=tick, creatures=[]))
        log.close()
        self.assertEqual(Session(self.tmp / 'ghost').player_track(), [])
        self.assertEqual(len(Session(self.tmp / 'ghost').world_track()), 60, 'the world is still read')
        world = Ecosystem(self.tmp / 'ghost', brain=EcoliBrain(seed=1), seed=2, ecoli=2, jelly=2)
        self.assertFalse(world.pilot)
        world.run(3.0)
        self.assertEqual(len(world.prey), 1, 'the recorded wreck is there')
        self.assertNotIn('player', world.rows[0]['observe'])

    def test_a_run_with_no_pilot_has_no_player_and_still_runs(self):
        world = Ecosystem(self.session, brain=EcoliBrain(seed=1), seed=2, pilot=False)
        result = world.run(2.0)
        self.assertEqual(result['ticks'], 60)
        self.assertNotIn('player', world.rows[0]['observe'])

    def test_a_child_takes_the_next_free_slot_of_its_kind_and_a_full_pool_refuses(self):
        world = Ecosystem(self.session, brain=EcoliBrain(seed=1), seed=3, ecoli=2, jelly=2,
                          ecoli_capacity=3, jelly_capacity=3)
        parent = world.alive('jelly')[0]
        child = world.place('jelly', near=parent, parent=parent.id)
        self.assertIsNotNone(child)
        self.assertEqual(child.parent, parent.id)
        self.assertGreaterEqual(child.id, 3)
        self.assertIsNone(world.place('jelly', near=parent, parent=parent.id), 'pool of three is full')
        self.assertEqual(world.births['jelly'], 1)


class LaneInPythonTests(unittest.TestCase):
    def test_the_lane_matches_the_protocol_examples(self):
        a = [-40.0, -40.0, 40.0, 40.0]
        v = lambda x, z: current.velocity(x, z, 0.0, a, 0.0, lane=2.0)
        for (x, z), want in (((0, -26), (2, 0)), ((26, 0), (0, 2)), ((0, 26), (-2, 0)), ((0, 0), (0, 0))):
            got = v(x, z)
            self.assertAlmostEqual(got[0], want[0], places=3, msg=f'{x},{z}')
            self.assertAlmostEqual(got[1], want[1], places=3, msg=f'{x},{z}')
        self.assertAlmostEqual(v(0, -18.4)[0], 0.066, places=2)
        self.assertLess(abs(v(0, -40)[1]), 1e-3)
        both = current.velocity(0, -26, 0.0, a, 1.0, wobble=0.0, mix=0.0, lane=2.0)
        alone = current.velocity(0, -26, 0.0, a, 1.0, wobble=0.0, mix=0.0)
        self.assertAlmostEqual(both[0], alone[0] + 2.0, places=3)
        got = current.from_hello(dict(infestation=dict(currentSpeed=0, laneSpeed=2, laneRadius=0.5)))
        self.assertEqual(got['lane'], 2.0)
        self.assertEqual(got['lane_radius'], 0.5)


if __name__ == '__main__':
    unittest.main()
