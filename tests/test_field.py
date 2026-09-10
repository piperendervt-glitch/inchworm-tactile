import math
import unittest

from creature_sim.field import FOOD, PATH, StigmergyField

BOUNDS = [-40.0, -40.0, 40.0, 40.0]


class FieldTests(unittest.TestCase):
    def test_deposit_is_strongest_where_it_was_written(self):
        field = StigmergyField(BOUNDS)
        # A cell centre, so the whole amount lands in one cell.
        cx = BOUNDS[0] + field.cell_size_x * 10.5
        cz = BOUNDS[1] + field.cell_size_z * 20.5
        field.deposit(cx, cz, FOOD, 5.0)

        here = field.sample(cx, cz, FOOD)
        self.assertAlmostEqual(here, 5.0, places=9)
        self.assertAlmostEqual(field.total(FOOD), 5.0, places=9)

        # Every other point in a ring around it reads lower.
        for step in (1, 2, 5):
            for angle in range(0, 360, 45):
                r = step * field.cell_size_x
                x = cx + r * math.cos(math.radians(angle))
                z = cz + r * math.sin(math.radians(angle))
                self.assertLess(field.sample(x, z, FOOD), here)

        # The other channel is untouched.
        self.assertEqual(field.total(PATH), 0.0)

    def test_update_decays_monotonically_at_the_stated_half_life(self):
        field = StigmergyField(BOUNDS)
        cx = BOUNDS[0] + field.cell_size_x * 32.5
        cz = BOUNDS[1] + field.cell_size_z * 32.5
        field.deposit(cx, cz, FOOD, 100.0)
        field.deposit(cx, cz, PATH, 100.0)

        food = [field.total(FOOD)]
        path = [field.total(PATH)]
        for _ in range(11 * 30):
            field.update(1 / 30)
            food.append(field.total(FOOD))
            path.append(field.total(PATH))

        for series in (food, path):
            for earlier, later in zip(series, series[1:]):
                self.assertLess(later, earlier)

        # 11 s is one food half life and well under one path half life.
        self.assertAlmostEqual(food[-1] / food[0], 0.5, places=6)
        self.assertAlmostEqual(path[-1] / path[0], 0.5 ** (11.0 / 140.0), places=6)

    def test_diffusion_conserves_the_total(self):
        field = StigmergyField(BOUNDS, cols=16, rows=16, half_life=(None, None))
        field.deposit(BOUNDS[0] + field.cell_size_x * 4.5,
                      BOUNDS[1] + field.cell_size_z * 4.5, FOOD, 7.0)
        # A corner cell, to exercise the border where a cell has two neighbours.
        field.deposit(BOUNDS[0] + field.cell_size_x * 0.5,
                      BOUNDS[1] + field.cell_size_z * 0.5, FOOD, 3.0)
        start = field.total(FOOD)
        self.assertAlmostEqual(start, 10.0, places=9)

        peak = max(field.cells[FOOD])
        for _ in range(200):
            field.update(1 / 30)
            self.assertAlmostEqual(field.total(FOOD), start, places=9)

        # Conserved, but spread out: the peak drops and more cells are wet.
        self.assertLess(max(field.cells[FOOD]), peak)
        self.assertGreater(sum(1 for v in field.cells[FOOD] if v > 1e-9), 2)

    def test_to_bytes_is_row_major_and_clamped(self):
        field = StigmergyField(BOUNDS, cols=8, rows=8, half_life=(None, None))
        col, row = 5, 2
        x = BOUNDS[0] + field.cell_size_x * (col + 0.5)
        z = BOUNDS[1] + field.cell_size_z * (row + 0.5)
        field.deposit(x, z, FOOD, 2.0)

        raw = field.to_bytes(FOOD, scale=1.0)
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[row * 8 + col], 255)
        self.assertEqual(sum(1 for v in raw if v), 1)

        half = field.to_bytes(FOOD, scale=4.0)
        self.assertEqual(half[row * 8 + col], 127)

    def test_rejects_bad_geometry(self):
        for bounds in ([-1, -1, -1, 1], [0, 0, 1], [0, 0, float('inf'), 1]):
            with self.assertRaises(ValueError):
                StigmergyField(bounds)
        with self.assertRaises(ValueError):
            StigmergyField(BOUNDS, cols=1)
        with self.assertRaises(ValueError):
            StigmergyField(BOUNDS, half_life=(-1.0, 1.0))
        with self.assertRaises(ValueError):
            StigmergyField(BOUNDS).update(-1.0)


if __name__ == '__main__':
    unittest.main()
