import random
import unittest

from creature_sim import current

ARENA = [-40.0, -40.0, 40.0, 40.0]
PERIOD = 90.0


def v(x, z, t=0.0, wobble=0.0, mix=0.0, speed=1.0):
    return current.velocity(x, z, t, ARENA, speed, PERIOD, wobble, mix)


class WorkedExampleTests(unittest.TestCase):
    """The same examples as CreatureCurrentTests.cs and docs/creature-protocol.md 5.2."""

    def near(self, expected, got, what):
        self.assertAlmostEqual(expected[0], got[0], places=3, msg=f'{what} x: {got}')
        self.assertAlmostEqual(expected[1], got[1], places=3, msg=f'{what} z: {got}')

    def test_without_wobble_the_arena_turns_anticlockwise(self):
        self.near((1, 0), v(0, -40), 'south edge')
        self.near((0, 1), v(40, 0), 'east edge')
        self.near((-1, 0), v(0, 40), 'north edge')
        self.near((0, -1), v(-40, 0), 'west edge')
        self.near((0, 0), v(0, 0), 'centre')
        self.near((2.5, 0), v(0, -40, speed=2.5), 'speed scales it')

    def test_the_wobble_moves_the_centre(self):
        self.near((-0.8, 0), v(0, 0, 0.0, 0.4), 'centre at t=0')
        self.near((1.8, 0), v(0, -40, 0.0, 0.4), 'south edge at t=0')
        self.near((-0.2, 0), v(0, 40, 0.0, 0.4), 'north edge at t=0')
        self.near((0, 0.8), v(0, 0, PERIOD / 4, 0.4), 'centre a quarter period on')
        self.near((0, 0.8), v(0, 0, PERIOD / 4, 0.4, 0.5), 'centre with mix')
        self.near((1.8, 0), v(0, -40, 0.0, 0.4, 0.5), 'south edge at t=0 with mix')

    def test_the_mix_passes_things_between_rings(self):
        t = current.MIX_PERIOD_RATIO * PERIOD / 4
        self.near((0, 0.7071), v(20, 0, t, 0.0, 0.0), 'whirl alone')
        self.near((1.0, 0.7071), v(20, 0, t, 0.0, 0.5), 'with mix 0.5')

    def test_nothing_presses_into_a_wall(self):
        for t in (0.0, 13.0, 47.5, 80.0, 151.0):
            for p in range(-40, 41, 5):
                self.assertAlmostEqual(v(-40, p, t, 0.8, 1.0, 3)[0], 0.0, places=6)
                self.assertAlmostEqual(v(40, p, t, 0.8, 1.0, 3)[0], 0.0, places=6)
                self.assertAlmostEqual(v(p, -40, t, 0.8, 1.0, 3)[1], 0.0, places=6)
                self.assertAlmostEqual(v(p, 40, t, 0.8, 1.0, 3)[1], 0.0, places=6)

    def test_no_drains_and_no_sources(self):
        h = 1e-4
        for t in (0.0, 22.0, 61.0, 133.0):
            for x in range(-35, 36, 7):
                for z in range(-35, 36, 7):
                    div = ((v(x + h, z, t, 0.6, 0.8, 2)[0] - v(x - h, z, t, 0.6, 0.8, 2)[0]) / (2 * h)
                           + (v(x, z + h, t, 0.6, 0.8, 2)[1] - v(x, z - h, t, 0.6, 0.8, 2)[1]) / (2 * h))
                    self.assertAlmostEqual(div, 0.0, places=5, msg=f'at {x},{z} t {t}')

    def test_off_means_off(self):
        self.assertEqual(v(10, -20, 5, 0.4, 0.5, 0.0), (0.0, 0.0))
        self.assertEqual(current.velocity(0, 0, 0, None, 1.0), (0.0, 0.0))


def advect(points, seconds, dt=0.5, wobble=0.4, mix=0.5, speed=1.0, visits=None):
    """Carry points along with a midpoint step, clamped to the arena as the walls would."""
    t = 0.0
    for _ in range(int(seconds / dt)):
        moved = []
        for i, (x, z) in enumerate(points):
            vx, vz = v(x, z, t, wobble, mix, speed)
            mx, mz = x + vx * dt / 2, z + vz * dt / 2
            vx, vz = v(mx, mz, t + dt / 2, wobble, mix, speed)
            x = min(40.0, max(-40.0, x + vx * dt))
            z = min(40.0, max(-40.0, z + vz * dt))
            moved.append((x, z))
            if visits is not None:
                visits[i].add((min(3, int((x + 40) // 20)), min(3, int((z + 40) // 20))))
        points = moved
        t += dt
    return points


class RoundTheWholeArenaTests(unittest.TestCase):
    """Nothing collects anywhere, and everything is carried through most of the arena."""

    def test_an_even_spread_stays_even(self):
        rng = random.Random(3)
        start = [(rng.uniform(-40, 40), rng.uniform(-40, 40)) for _ in range(400)]
        end = advect(start, 600.0, dt=1.0)
        counts = [0] * 16
        for x, z in end:
            counts[min(3, int((z + 40) // 20)) * 4 + min(3, int((x + 40) // 20))] += 1
        share = [c / len(end) for c in counts]
        # Evenly spread is 1/16 = 6.25% per quarter-arena cell.
        self.assertLess(max(share), 0.12, f'something gathered: {counts}')
        self.assertGreater(min(share), 0.02, f'something emptied: {counts}')

    def test_every_start_is_carried_through_most_of_the_arena(self):
        # The centre, near the centre, a ring halfway out and near the walls.
        starts = [(0.0, 0.0), (3.0, 0.0), (0.0, -8.0), (-12.0, 12.0), (20.0, -20.0),
                  (30.0, 5.0), (-35.0, -35.0), (36.0, 36.0)]
        visits = [set() for _ in starts]
        advect(list(starts), 900.0, dt=1.0, visits=visits)
        counts = [len(s) for s in visits]
        # Sixteen quarter-arena cells; a lone whirl keeps a start near the
        # centre inside the middle four.
        self.assertGreaterEqual(min(counts), 10, f'cells visited from each start: {counts}')

    def test_without_mix_each_start_keeps_to_its_ring(self):
        # The reason the mix exists: without it the centre stays in the middle four cells.
        visits = [set()]
        advect([(0.0, 0.0)], 900.0, dt=1.0, mix=0.0, visits=visits)
        self.assertLessEqual(len(visits[0]), 4)

    def test_settings_from_a_hello(self):
        self.assertIsNone(current.from_hello({}))
        self.assertIsNone(current.from_hello(dict(infestation=dict(currentSpeed=0))))
        got = current.from_hello(dict(infestation=dict(currentSpeed=1.5, currentPeriod=60,
                                                       currentWobble=0.3, currentMix=0.25)))
        self.assertEqual(got, dict(speed=1.5, period=60.0, wobble=0.3, mix=0.25))
        self.assertAlmostEqual(current.top_speed(got), 1.5 * (1 + 0.6 + 0.5))


if __name__ == '__main__':
    unittest.main()
