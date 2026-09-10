import json
import unittest
from pathlib import Path

from creature_sim import protocol
from creature_sim.ecoli.brain import EcoliBrain
from creature_sim.field import FOOD, StigmergyField

SAMPLES = Path(__file__).resolve().parents[1] / 'docs' / 'creature-protocol-samples'
NAMES = ('hello', 'welcome', 'observe', 'command', 'field', 'bye', 'error')


def sample(name):
    return json.loads((SAMPLES / (name + '.json')).read_text(encoding='utf-8'))


class SampleTests(unittest.TestCase):
    def test_every_sample_exists_and_is_accepted(self):
        for name in NAMES:
            with self.subTest(name):
                data = sample(name)
                parsed = protocol.parse(json.dumps(data).encode('utf-8'))
                self.assertEqual(parsed.get('type'), name,
                                 f'{name}.json was rejected: {parsed.get("message")}')
                self.assertEqual(parsed, data)

    def test_samples_fit_a_datagram(self):
        for name in NAMES:
            with self.subTest(name):
                self.assertLessEqual(len(protocol.encode(sample(name))), protocol.MAX_DATAGRAM)

    def test_field_sample_decodes_to_the_declared_grid(self):
        message = sample('field')
        raw = protocol.decode_field_data(message)
        self.assertEqual(len(raw), message['cols'] * message['rows'])
        # Row major: docs/creature-protocol.md 6.5 puts cell (col, row) at
        # data[row * cols + col], and the fixture stores (row * 4 + col) % 256.
        for col, row in ((0, 0), (5, 2), (63, 63)):
            self.assertEqual(raw[row * message['cols'] + col], (row * 4 + col) % 256)


class ParseTests(unittest.TestCase):
    def test_version_mismatch_is_reported_not_raised(self):
        data = sample('observe')
        data['v'] = 2
        parsed = protocol.parse(json.dumps(data).encode('utf-8'))
        self.assertEqual(parsed['type'], 'error')
        self.assertEqual(parsed['code'], protocol.VERSION_MISMATCH)
        self.assertEqual(parsed['session'], data['session'])

    def test_garbage_never_raises(self):
        for payload in (b'', b'{', b'null', b'[]', b'\xff\xfe', b'{"v":1}',
                        json.dumps(dict(v=1, type='nope', session='s')).encode()):
            with self.subTest(payload=payload):
                parsed = protocol.parse(payload)
                self.assertEqual(parsed['type'], 'error')
                self.assertIn(parsed['code'], (protocol.MALFORMED, protocol.VERSION_MISMATCH))

    def test_oversized_datagram_is_refused(self):
        parsed = protocol.parse(b'x' * (protocol.MAX_DATAGRAM + 1))
        self.assertEqual(parsed['code'], protocol.MALFORMED)

    def test_observe_needs_sixteen_cells(self):
        data = sample('observe')
        data['creatures'][0]['cells'] = data['creatures'][0]['cells'][:15]
        self.assertEqual(protocol.parse(json.dumps(data).encode())['code'], protocol.MALFORMED)

    def test_observe_rejects_repeated_ids_and_bad_kinds(self):
        data = sample('observe')
        data['creatures'][1]['id'] = data['creatures'][0]['id']
        self.assertEqual(protocol.parse(json.dumps(data).encode())['code'], protocol.MALFORMED)

        data = sample('observe')
        data['creatures'][0]['cells'][0]['k'] = 9
        self.assertEqual(protocol.parse(json.dumps(data).encode())['code'], protocol.MALFORMED)

    def test_hello_rejects_a_degenerate_arena(self):
        data = sample('hello')
        data['arena']['bounds'] = [10, -40, -10, 40]
        self.assertEqual(protocol.parse(json.dumps(data).encode())['code'], protocol.MALFORMED)

    def test_command_rejects_an_unknown_mode(self):
        data = sample('command')
        data['creatures'][0]['mode'] = 'sprint'
        self.assertEqual(protocol.parse(json.dumps(data).encode())['code'], protocol.MALFORMED)


class BuilderTests(unittest.TestCase):
    def test_welcome_matches_the_sample_shape(self):
        brain = EcoliBrain(seed=0)
        built = protocol.make_welcome('s', brain)
        self.assertEqual(set(built), set(sample('welcome')))
        self.assertEqual(set(built['brain']), set(sample('welcome')['brain']))
        self.assertEqual(protocol.parse(json.dumps(built).encode())['type'], 'welcome')

    def test_command_matches_the_sample_shape(self):
        built = protocol.make_command('s', 7, [
            dict(id=3, mode='run', speed=1.0, turn=0.0, deposit=0.2, energy=0.6, starved=False)])
        self.assertEqual(set(built['creatures'][0]), set(sample('command')['creatures'][0]))
        self.assertEqual(protocol.parse(json.dumps(built).encode())['type'], 'command')

    def test_field_matches_the_sample_shape_and_round_trips(self):
        field = StigmergyField([-40.0, -40.0, 40.0, 40.0])
        # A cell centre, so the whole amount lands in one cell. The arena
        # centre would sit on a corner and split four ways.
        col, row = 31, 40
        x = -40.0 + field.cell_size_x * (col + 0.5)
        z = -40.0 + field.cell_size_z * (row + 0.5)
        field.deposit(x, z, FOOD, 4.0)
        built = protocol.make_field('s', 7, field, FOOD, scale=4.0)

        self.assertEqual(set(built), set(sample('field')))
        self.assertEqual(built['cellSize'], 1.25)
        self.assertEqual(built['origin'], [-40.0, -40.0])
        raw = protocol.decode_field_data(built)
        self.assertEqual(max(raw), 255)
        self.assertEqual(raw[row * built['cols'] + col], 255)
        self.assertEqual(sum(1 for v in raw if v), 1)
        self.assertLessEqual(len(protocol.encode(built)), protocol.MAX_DATAGRAM)

    def test_encode_refuses_to_send_an_oversized_message(self):
        huge = protocol.make_error('malformed', 'x', 's')
        huge['padding'] = 'y' * protocol.MAX_DATAGRAM
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode(huge)

    def test_a_full_twelve_creature_observe_still_fits(self):
        creature = sample('observe')['creatures'][0]
        data = sample('observe')
        data['creatures'] = []
        for i in range(12):
            entry = json.loads(json.dumps(creature))
            entry['id'] = i
            entry['state'] = 'alive'
            data['creatures'].append(entry)
        payload = protocol.encode(data)
        self.assertLessEqual(len(payload), protocol.MAX_DATAGRAM)
        self.assertEqual(protocol.parse(payload)['type'], 'observe')


if __name__ == '__main__':
    unittest.main()
