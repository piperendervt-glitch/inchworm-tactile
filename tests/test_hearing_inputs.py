"""Hearing reaching the Brain, old brains learning they have ears, and other food."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim import hearing, protocol
from creature_sim.ecoli.brain import EAR_COUNT, EARS, INPUTS, LEGACY_INPUTS, STATE, EcoliBrain
from creature_sim.ecoli.colony import Colony
from creature_sim.replay import ReplayWorld
from creature_sim.server import SessionLog

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'docs' / 'creature-protocol-samples'
BOUNDS = [-40.0, -40.0, 40.0, 40.0]
HZ = 30


def sample(name):
    return json.loads((SAMPLES / (name + '.json')).read_text(encoding='utf-8'))


def quiet_cells():
    return [dict(p=0.0, k=0, h=0.0) for _ in range(16)]


def creature(**kw):
    out = dict(id=0, state='alive', pos=[0.0, 0.0, 0.0], heading=0.0, speed=0.0,
               hp=120, eating=False, cells=quiet_cells())
    out.update(kw)
    return out


def legacy_dict(seed=4):
    """A brain file as it was saved before hearing: 100 inputs."""
    data = EcoliBrain(seed=seed).to_dict()
    data['w'] = [row[:LEGACY_INPUTS] + row[INPUTS:] for row in data['w']]
    data['inputs'] = LEGACY_INPUTS
    data['fingerprint'] = hashlib.sha256(
        json.dumps([data['w'], data['out']], sort_keys=True).encode()).hexdigest()
    return data


class HearingInputTests(unittest.TestCase):
    def test_ears_land_in_their_own_inputs(self):
        colony = Colony(bounds=BOUNDS, seed=1)
        colony.step(dict(tick=0, creatures=[creature(state='spawned')]))
        inputs, _ = colony.build_inputs(colony.creatures[0],
                                        creature(ears=[0.5, 0.25, 0.0, 1.0]))
        self.assertEqual(len(inputs), INPUTS)
        self.assertEqual(inputs[EARS:EARS + EAR_COUNT], [0.5, 0.25, 0.0, 1.0])

    def test_a_body_without_ears_leaves_the_creature_deaf(self):
        colony = Colony(bounds=BOUNDS, seed=1)
        colony.step(dict(tick=0, creatures=[creature(state='spawned')]))
        inputs, _ = colony.build_inputs(colony.creatures[0], creature())
        self.assertEqual(inputs[EARS:EARS + EAR_COUNT], [0.0] * EAR_COUNT)


class LegacyBrainTests(unittest.TestCase):
    def test_a_hundred_input_brain_is_widened_with_silent_ears(self):
        old = legacy_dict()
        brain = EcoliBrain.from_dict(old)
        self.assertEqual(len(brain.w), STATE)
        self.assertTrue(all(len(row) == INPUTS + STATE for row in brain.w))
        for row, before in zip(brain.w, old['w']):
            self.assertEqual(row[EARS:EARS + EAR_COUNT], [0.0] * EAR_COUNT)
            self.assertEqual(row[:LEGACY_INPUTS], before[:LEGACY_INPUTS])
            self.assertEqual(row[INPUTS:], before[LEGACY_INPUTS:])
        self.assertEqual(brain.migrated_from,
                         dict(inputs=LEGACY_INPUTS, fingerprint=old['fingerprint']))

    def test_the_widened_brain_ignores_what_it_hears(self):
        brain = EcoliBrain.from_dict(legacy_dict())
        base = [0.1 * (i % 5) for i in range(INPUTS)]
        loud = list(base)
        loud[EARS:EARS + EAR_COUNT] = [1.0] * EAR_COUNT
        state = brain.new_state()
        self.assertEqual(brain.step(base, state), brain.step(loud, state))

    def test_migration_survives_a_save_and_load(self):
        brain = EcoliBrain.from_dict(legacy_dict())
        again = EcoliBrain.from_json(brain.to_json())
        self.assertEqual(again.fingerprint, brain.fingerprint)
        self.assertEqual(again.migrated_from, brain.migrated_from)

    def test_a_damaged_legacy_file_is_refused(self):
        old = legacy_dict()
        old['w'][0][0] += 1.0
        with self.assertRaises(ValueError):
            EcoliBrain.from_dict(old)

    @unittest.skipUnless((ROOT / 'brains' / 'ecoli-gen-005.json').exists(),
                         'generation five is not on this machine')
    def test_generation_five_still_loads(self):
        brain = EcoliBrain.from_json(str(ROOT / 'brains' / 'ecoli-gen-005.json'))
        self.assertEqual(brain.generation, 5)
        self.assertTrue(all(len(row) == INPUTS + STATE for row in brain.w))


class HearingMessageTests(unittest.TestCase):
    def observe(self, **kw):
        return dict(v=1, type='observe', session='s', tick=1, dt=1 / HZ,
                    creatures=[creature(**kw)])

    def test_good_ears_pass(self):
        message = self.observe(ears=[0.0, 0.5, 1.0, 0.25])
        message['sounds'] = [dict(kind='player', pos=[0, 0, 0], level=0.4)]
        message['prey'] = []
        self.assertEqual(protocol.parse(json.dumps(message))['type'], 'observe')

    def test_bad_ears_are_malformed(self):
        for ears in ([0.1, 0.2, 0.3], [0.0, 0.0, 0.0, 1.5], 'loud'):
            got = protocol.parse(json.dumps(self.observe(ears=ears)))
            self.assertEqual(got['type'], 'error', ears)

    def test_sounds_and_prey_must_be_lists(self):
        for key in ('sounds', 'prey'):
            message = self.observe()
            message[key] = {}
            self.assertEqual(protocol.parse(json.dumps(message))['type'], 'error', key)

    def test_welcome_reports_the_real_input_count(self):
        welcome = protocol.make_welcome('s', EcoliBrain(seed=0))
        self.assertEqual(welcome['brain']['inputs'], INPUTS)


class ReplayFoodAndHearingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, prey=None, sounds=None, seconds=2.0):
        hello = sample('hello')
        hello['arena']['bounds'] = BOUNDS
        hello['arena']['obstacles'] = []
        log = SessionLog(self.tmp / 'session')
        log.meta(hello, sample('welcome'))
        for tick in range(int(seconds * HZ)):
            observe = dict(v=1, type='observe', session='s', tick=tick, t=round(tick / HZ, 4),
                           dt=1 / HZ,
                           player=dict(pos=[-30.0, 0.0, -30.0], heading=0.0, hp=250, guard=False),
                           creatures=[])
            if prey is not None:
                observe['prey'] = prey
            if sounds is not None:
                observe['sounds'] = sounds
            log.write(observe, dict(v=1, type='command', session='s', tick=tick, creatures=[]))
        log.close()
        return self.tmp / 'session'

    def test_a_recorded_wreck_is_food(self):
        wreck = dict(kind='wreck', id=0, pos=[10.0, 0.0, 10.0], hp=300, maxHp=300)
        world = ReplayWorld(self.write(prey=[wreck], sounds=[]), count=1, seed=0)
        world.update_world(0.0)
        body = world.bodies[0]
        body.x, body.z, body.heading = 10.0, 10.0, 0.0
        world.sense(body, world.bodies)
        self.assertTrue(body.eating)
        observation = world.observation()
        self.assertEqual(observation['prey'][0]['kind'], 'wreck')
        # The log said nothing was making noise, so nothing is heard.
        self.assertEqual(observation['creatures'][0]['ears'], [0.0] * 4)

    def test_an_old_log_still_lets_the_pilot_be_heard(self):
        world = ReplayWorld(self.write(), count=1, seed=0)
        world.update_world(0.0)
        body = world.bodies[0]
        # Facing +Z with the parked pilot 8 m behind: back ear hears
        # player_level(0) / (1 + 1) = 0.2 / 2 = 0.1.
        body.x, body.z, body.heading = -30.0, -22.0, 0.0
        ears = world.observation()['creatures'][0]['ears']
        expected = hearing.player_level(0.0) / 2.0
        for got, want in zip(ears, [0.0, 0.0, expected, 0.0]):
            self.assertAlmostEqual(got, want, places=4)

    def test_a_replay_with_food_and_sound_runs(self):
        wreck = dict(kind='wreck', id=0, pos=[0.0, 0.0, 0.0], hp=300, maxHp=300)
        sound = dict(kind='mech', pos=[5.0, 0.0, 5.0], level=0.45)
        result = ReplayWorld(self.write(prey=[wreck], sounds=[sound]), count=3, seed=2).run()
        # The run lasts until the last recorded tick, 59 / 30 s.
        self.assertEqual(result['ticks'], 59)


if __name__ == '__main__':
    unittest.main()
