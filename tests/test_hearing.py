import math
import unittest

from creature_sim import hearing


def source(x, z, level=1.0, kind='player'):
    return dict(kind=kind, pos=[x, 0.0, z], level=level)


class WorkedExampleTests(unittest.TestCase):
    """The examples in docs/creature-protocol.md 5.1, computed by hand first."""

    def check(self, got, expected):
        self.assertEqual(len(got), 4)
        for i, (a, b) in enumerate(zip(got, expected)):
            self.assertAlmostEqual(a, b, places=3, msg=f'ear {i}: {got} vs {expected}')

    def test_straight_ahead_at_eight_metres(self):
        # distance 8 -> 1 / (1 + 1) = 0.5, bearing 0 -> front only
        self.check(hearing.ears(0.0, 0.0, 0.0, [source(0.0, 8.0)]), [0.5, 0.0, 0.0, 0.0])

    def test_to_the_right_at_eight_metres(self):
        # bearing atan2(8, 0) = pi/2 -> the right ear
        self.check(hearing.ears(0.0, 0.0, 0.0, [source(8.0, 0.0)]), [0.0, 0.5, 0.0, 0.0])

    def test_turning_to_face_it_moves_it_to_the_front(self):
        self.check(hearing.ears(0.0, 0.0, math.pi / 2, [source(8.0, 0.0)]), [0.5, 0.0, 0.0, 0.0])

    def test_close_behind(self):
        # distance 4 -> 1 / (1 + 0.25) = 0.8
        self.check(hearing.ears(0.0, 0.0, 0.0, [source(0.0, -4.0)]), [0.0, 0.0, 0.8, 0.0])

    def test_diagonal_splits_between_two_ears(self):
        d = 8.0 / math.sqrt(2.0)
        # 0.5 * cos(45 degrees) = 0.3536 on the front and the right
        self.check(hearing.ears(0.0, 0.0, 0.0, [source(d, d)]), [0.3536, 0.3536, 0.0, 0.0])

    def test_nothing_beyond_range(self):
        self.check(hearing.ears(0.0, 0.0, 0.0, [source(0.0, hearing.RANGE + 1.0)]), [0.0] * 4)


class ShapeTests(unittest.TestCase):
    def test_sounds_add_up_and_clamp(self):
        loud = [source(0.0, 1.0) for _ in range(5)]
        self.assertEqual(hearing.ears(0.0, 0.0, 0.0, loud)[0], 1.0)

    def test_quiet_and_empty_inputs_hear_nothing(self):
        self.assertEqual(hearing.ears(0.0, 0.0, 0.0, []), [0.0] * 4)
        self.assertEqual(hearing.ears(0.0, 0.0, 0.0, None), [0.0] * 4)
        self.assertEqual(hearing.ears(0.0, 0.0, 0.0, [source(0.0, 3.0, level=0.0)]), [0.0] * 4)

    def test_a_source_on_top_reaches_every_ear(self):
        got = hearing.ears(2.0, 2.0, 1.0, [source(2.0, 2.0, level=0.3)])
        self.assertEqual(got, [0.3] * 4)

    def test_pilot_loudness(self):
        self.assertAlmostEqual(hearing.player_level(0.0), 0.2)
        self.assertAlmostEqual(hearing.player_level(12.0), 0.7)
        self.assertAlmostEqual(hearing.player_level(6.0), 0.45)
        self.assertEqual(hearing.player_level(30.0, boosting=True, firing=0.6), 1.0)


if __name__ == '__main__':
    unittest.main()
