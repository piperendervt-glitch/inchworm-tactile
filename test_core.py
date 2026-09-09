import csv
import tempfile
import unittest
from pathlib import Path
from core import World, LocalNCA, Recorder, read_config


class CoreTests(unittest.TestCase):
    def test_deterministic_and_finite(self):
        a, b = World(), World()
        for _ in range(300):
            self.assertEqual(a.step(), b.step())
        self.assertTrue(all(0 <= v <= 1 for v in a.belly + a.head))
        self.assertNotEqual(a.x, 0.)

    def test_sensor_delay(self):
        config = read_config()
        config['sensor']['noise'] = 0
        w = World(config)
        baseline = w.belly[:]
        w.step([0] * 6)
        w.step([0] * 6)
        self.assertEqual(w.belly, baseline)
        w.step([0] * 6)
        self.assertGreater(max(w.belly), max(baseline))

    def test_locality(self):
        a, b = LocalNCA(), LocalNCA()
        x, y = [.12] * 45, [.12] * 45
        y[-9:] = [1.] * 9
        a.step(x, [.12] * 9)
        b.step(y, [1.] * 9)
        self.assertEqual(a.state[:4], b.state[:4])
        self.assertNotEqual(a.state[4], b.state[4])

    def test_record_schema_and_causal_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.csv'
            w = World()
            before = w.belly[:] + w.head[:]
            r = Recorder(path, w)
            row = w.step()
            r.write(row)
            r.close()
            with path.open() as f:
                self.assertTrue(f.readline().startswith('# '))
                rows = list(csv.reader(f))
            self.assertEqual(len(rows[0]), len(rows[1]))
            self.assertEqual(row[2:56], before)

    def test_food_harm_and_death(self):
        cfg = read_config()
        cfg['environment'] = [{'kind': 'food', 'x': .239, 'y': 0, 'radius': .06, 'height': .04}]
        w = World(cfg)
        w.hp = 50
        for _ in range(35):
            w.step([0] * 6)
        self.assertTrue(w.objects[0]['eaten'])
        self.assertGreater(w.hp, 70)
        cfg['environment'][0]['kind'] = 'harm'
        w = World(cfg)
        w.hp = .01
        w.step([0] * 6)
        self.assertEqual(w.hp, 0)
        self.assertIsNone(w.step())


if __name__ == '__main__':
    unittest.main()
