import math
import unittest
from panda3d.core import Vec3
from core import World
from body_limits import ANGLE_LIMITS


class RangeTests(unittest.TestCase):
    def test_both_joints_bend_120_in_all_directions(self):
        for axis in (1,2,4,5):
            for sign in (-1,1):
                with self.subTest(axis=axis,sign=sign):
                    w=World();w.mechanics.world.setGravity(Vec3(0))
                    for p in w.mechanics.segments:p.setZ(p.getZ()+1)
                    targets=[0.]*6;targets[axis]=sign*math.radians(120)
                    for _ in range(120):
                        w.step(targets)
                        for a,b in zip(w.mechanics.segments,w.mechanics.segments[1:]):
                            contacts=w.mechanics.world.contactTestPair(a.node(),b.node()).getContacts()
                            self.assertFalse(any(c.getManifoldPoint().getDistance()<-.0001 for c in contacts),
                                             'Adjacent segment penetration during bending')
                    self.assertAlmostEqual(math.degrees(w.angles[axis]),sign*120,delta=1)
                    self.assertTrue(all(0<=v<=1 for v in w.observation().flatten()))

    def test_commands_are_clamped_to_axis_limits(self):
        w=World();w.step([10.]*6)
        self.assertEqual(w.targets,list(ANGLE_LIMITS))
        w.step([-10.]*6)
        self.assertEqual(w.targets,[-v for v in ANGLE_LIMITS])
