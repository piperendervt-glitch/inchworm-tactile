import csv
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim import protocol
from creature_sim.ecoli.brain import EcoliBrain
from creature_sim.logs import Session, convert
from creature_sim.replay import ReplayWorld, evaluate, fitness
from creature_sim.server import SessionLog

SAMPLES = Path(__file__).resolve().parents[1] / 'docs' / 'creature-protocol-samples'
HZ = 30


def sample(name):
    return json.loads((SAMPLES / (name + '.json')).read_text(encoding='utf-8'))


def write_session(directory, seconds=8.0, walk=None, creatures=3, bounds=None):
    """A session log shaped exactly like the server writes one.

    ``walk`` gives the pilot's path as a function of time; the default keeps
    them parked in a corner, well away from anything.
    """
    hello = sample('hello')
    if bounds is not None:
        hello['arena']['bounds'] = bounds
        hello['arena']['obstacles'] = []
    walk = walk or (lambda t: (-30.0, -30.0))

    log = SessionLog(directory)
    log.meta(hello, sample('welcome'))
    cells = [dict(p=0.0, k=0, h=0.0) for _ in range(16)]
    for tick in range(int(seconds * HZ)):
        t = round(tick / HZ, 4)
        x, z = walk(t)
        observe = dict(v=1, type='observe', session='s', tick=tick, t=t, dt=1 / HZ,
                       player=dict(pos=[x, 0.0, z], heading=0.0, hp=250, guard=False),
                       creatures=[dict(id=i, state='spawned' if tick == 0 else 'alive',
                                       pos=[float(i) * 4, 0.0, 0.0], heading=0.0,
                                       speed=0.0, hp=120, eating=False,
                                       cells=[dict(c) for c in cells])
                                  for i in range(creatures)])
        command = dict(v=1, type='command', session='s', tick=tick,
                       creatures=[dict(id=i, mode='run', speed=1.0, turn=0.0,
                                       deposit=0.5, energy=1.0, starved=False)
                                  for i in range(creatures)])
        log.write(observe, command)
    log.close()
    return Path(directory)


class LogConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_session_becomes_the_four_training_tables(self):
        directory = write_session(self.tmp / 'session', seconds=2.0, creatures=2)
        written = convert(directory)

        self.assertEqual(set(written),
                         {'creature-0', 'creature-1', 'player', 'events', 'meta'})
        for path in written.values():
            self.assertTrue(path.exists(), path)

        player = list(csv.DictReader(written['player'].read_text(encoding='utf-8').splitlines()))
        self.assertEqual(len(player), 60)
        self.assertEqual(player[0]['hp'], '250')

        creature = list(csv.DictReader(written['creature-0'].read_text(encoding='utf-8').splitlines()))
        self.assertEqual(len(creature), 60)
        self.assertEqual(creature[0]['state'], 'spawned')
        self.assertEqual(creature[1]['state'], 'alive')
        self.assertEqual(creature[0]['mode'], 'run')

        events = list(csv.DictReader(written['events'].read_text(encoding='utf-8').splitlines()))
        self.assertEqual([e['type'] for e in events], ['spawn', 'spawn'])

        meta = json.loads(written['meta'].read_text(encoding='utf-8'))
        self.assertEqual(meta['ticks'], 60)
        self.assertEqual(meta['creatures'], [0, 1])
        self.assertEqual(meta['arena']['bounds'], [-40, -40, 40, 40])

    def test_hits_meals_and_deaths_land_in_the_event_table(self):
        directory = self.tmp / 'events'
        log = SessionLog(directory)
        log.meta(sample('hello'), sample('welcome'))
        quiet = [dict(p=0.0, k=0, h=0.0) for _ in range(16)]

        def observe(tick, **kw):
            creature = dict(id=0, state='alive', pos=[0.0, 0.0, 0.0], heading=0.0,
                            speed=0.0, hp=120, eating=False,
                            cells=[dict(c) for c in quiet])
            creature.update(kw)
            return dict(v=1, type='observe', session='s', tick=tick, t=tick / HZ,
                        dt=1 / HZ,
                        player=dict(pos=[0.0, 0.0, 0.0], heading=0.0, hp=250, guard=False),
                        creatures=[creature])

        hurt = [dict(c) for c in quiet]
        hurt[3] = dict(p=1.0, k=1, h=0.4)
        dead = dict(state='dead', reason='shot')
        command = dict(v=1, type='command', session='s', tick=0, creatures=[])
        log.write(observe(0, state='spawned'), command)
        log.write(observe(1, cells=hurt), command)
        log.write(observe(2, eating=True), command)
        log.write(observe(3, eating=True), command)   # still eating, not a new meal
        log.write(observe(4, **dead), command)
        log.close()

        written = convert(directory)
        events = list(csv.DictReader(written['events'].read_text(encoding='utf-8').splitlines()))
        self.assertEqual([e['type'] for e in events],
                         ['spawn', 'hit', 'eat', 'die'])
        self.assertEqual(events[1]['value'], '0.4')
        self.assertEqual(events[3]['value'], 'shot')


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_recording_can_be_replayed_for_ten_seconds(self):
        directory = write_session(self.tmp / 'session', seconds=12.0)
        world = ReplayWorld(directory, count=6, seed=1)

        self.assertEqual(len(world.bodies), 6)
        self.assertEqual(world.bounds, [-40, -40, 40, 40])
        self.assertEqual(len(world.obstacles), 2)

        result = world.run(seconds=10.0)
        self.assertAlmostEqual(result['seconds'], 10.0, delta=0.1)
        self.assertEqual(result['ticks'], 300)
        self.assertEqual(result['creatures'], 6)
        self.assertGreater(result['path_m'], 0.0, 'nothing moved at all')
        self.assertIsInstance(fitness(result), float)

    def test_the_same_seed_gives_the_same_run(self):
        directory = write_session(self.tmp / 'session', seconds=6.0)
        first = ReplayWorld(directory, count=4, seed=7).run()
        again = ReplayWorld(directory, count=4, seed=7).run()
        self.assertEqual(first, again)

        other = ReplayWorld(directory, count=4, seed=8).run()
        self.assertNotEqual(first['path_m'], other['path_m'],
                            'a different seed should place them differently')

    def test_a_pilot_who_walks_over_them_is_eaten(self):
        # The pilot sweeps the arena, so contact does not depend on the
        # creatures having learned to hunt.
        def sweep(t):
            return (-40.0 + (t * 12.0) % 80.0, 0.0)

        directory = write_session(self.tmp / 'session', seconds=20.0, walk=sweep,
                                  bounds=[-40, -40, 40, 40])
        world = ReplayWorld(directory, count=6, seed=3)
        result = world.run()

        self.assertGreater(result['eating_s'], 0.0, 'the pilot never touched anything')
        self.assertGreater(result['reached'], 0)
        self.assertIsNotNone(result['first_contact_s'])
        # Eating is what the score is mostly made of.
        self.assertGreater(fitness(result), 0.0)

    def test_scoring_prefers_eating_sooner_and_walls_less(self):
        base = dict(seconds=30.0, creatures=6, alive=6, eating_s=0.0,
                    contact_s=0.0, first_contact_s=None, reached=0)
        nothing = fitness(base)
        ate = fitness(dict(base, eating_s=5.0))
        early = fitness(dict(base, eating_s=5.0, first_contact_s=1.0, reached=1))
        late = fitness(dict(base, eating_s=5.0, first_contact_s=25.0, reached=1))
        stuck = fitness(dict(base, contact_s=60.0))

        self.assertGreater(ate, nothing)
        self.assertGreater(early, late)
        self.assertLess(stuck, nothing)

    def test_the_pilot_follows_the_recorded_path(self):
        def line(t):
            return (t * 2.0 - 20.0, 5.0)

        directory = write_session(self.tmp / 'session', seconds=10.0, walk=line)
        world = ReplayWorld(directory, count=1, seed=0)

        for seconds, expected in ((0.0, -20.0), (5.0, -10.0), (9.9, -0.2)):
            x, z = world.player_at(seconds)
            self.assertAlmostEqual(x, expected, delta=0.2, msg=f'at {seconds}s')
            self.assertAlmostEqual(z, 5.0, delta=0.01)
        # Past the end it holds still rather than looping back.
        self.assertEqual(world.player_at(100.0), world.player_at(9.99))

    def test_evaluate_scores_a_brain_on_a_recording(self):
        directory = write_session(self.tmp / 'session', seconds=6.0)
        score, result = evaluate(directory, EcoliBrain(seed=2), count=3, seed=1)
        self.assertIsInstance(score, float)
        self.assertEqual(result['fingerprint'], EcoliBrain(seed=2).fingerprint)
        self.assertEqual(result['creatures'], 3)

    def test_a_session_with_no_pilot_track_is_refused(self):
        directory = self.tmp / 'empty'
        log = SessionLog(directory)
        log.meta(sample('hello'), sample('welcome'))
        log.write(dict(v=1, type='observe', tick=0, t=0.0, dt=1 / HZ, creatures=[]),
                  dict(v=1, type='command', tick=0, creatures=[]))
        log.close()
        with self.assertRaises(ValueError):
            ReplayWorld(directory)


if __name__ == '__main__':
    unittest.main()
