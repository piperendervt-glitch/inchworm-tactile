import math
import unittest
from core import World
from body_geometry import VERTICES,APOTHEM

class HexagonTests(unittest.TestCase):
    def test_shared_hull_has_flat_bottom(self):
        self.assertEqual(len(VERTICES),24)
        self.assertEqual(sum(abs(v[2]+APOTHEM)<1e-10 for v in VERTICES),4)
        w=World()
        self.assertTrue(all(p.node().getShape(0).getType().getName()=='BulletConvexHullShape' for p in w.mechanics.segments))
        self.assertTrue(all(abs(p[2]-.001)<1e-6 for p in w.mechanics.surface_points()))

    def test_friction_applies_to_middle_and_both_ends(self):
        w=World();w.step([0.]*6,manual_grips=(0.,1.))
        for p,mu in zip(w.mechanics.segments,(1.,1.,1.8)):
            self.assertAlmostEqual(p.node().getFriction(),mu,places=6)

    def test_seed17_does_not_roll_metres_sideways(self):
        w=World();start=w.mechanics.center()
        for _ in range(900):w.step()
        self.assertLess(abs(w.mechanics.center()[1]-start[1]),.03)
        self.assertTrue(all(math.isfinite(v) for p in w.nodes for v in p))
