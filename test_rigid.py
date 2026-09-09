import math
import unittest
from core import World,read_config
from panda3d.core import Vec3


class RigidTests(unittest.TestCase):
    def world(self):
        cfg=read_config();cfg['environment']=[];cfg['sensor']['noise']=0
        return World(cfg)

    def test_contact_load_balances_weight(self):
        w=self.world()
        for _ in range(30):w.step([0.]*6)
        self.assertAlmostEqual(sum(w.mechanics.loads),.10*9.81,delta=.15)
        self.assertGreater(min(w.mechanics.loads),.3)
        self.assertGreater(max(w.support_touch),.5)

    def test_three_segments_start_lying_without_feet(self):
        for seed in (0,17,18,99):
            cfg=read_config();cfg['controller']['seed']=seed;w=World(cfg);m=w.mechanics
            self.assertEqual(len(m.segments),3);self.assertEqual(len(m.joints),2)
            self.assertEqual(len(m.world.getRigidBodies()),5)  # floor + obstacle + 3 segments
            self.assertFalse(hasattr(m,'legs'))
            self.assertTrue(all(.018<=p.getZ()<=.020 for p in m.segments))
            self.assertEqual(w.angles,[0.]*6)

    def test_joints_bend_from_floor_without_pose_rollback(self):
        w=self.world();before=w.controller.weights
        for _ in range(30):w.step([0,.6,0,0,-.6,0])
        self.assertGreater(w.angles[1],.4);self.assertLess(w.angles[4],-.4)
        self.assertGreater(max(p[2] for p in w.nodes)-min(p[2] for p in w.nodes),.01)
        self.assertEqual(before,w.controller.weights)

    def test_grip_requires_contact_and_releases(self):
        w=self.world();m=w.mechanics
        for p in m.segments:p.setZ(p.getZ()+.3)
        w.step([0.]*6,manual_grips=(1.,1.))
        self.assertEqual(m.anchors,[None,None]);self.assertEqual(m.loads,[0.,0.])
        for _ in range(60):w.step([0.]*6,manual_grips=(1.,1.))
        self.assertTrue(any(a is not None for a in m.anchors))
        self.assertTrue(all(f<=m.settings['grip_force_n']+1e-5 for f in m.forces))
        w.step([0.]*6,manual_grips=(0.,0.))
        self.assertEqual(m.anchors,[None,None])

    def test_gravity_and_floor_prevent_free_fall(self):
        w=self.world();m=w.mechanics
        for p in m.segments:p.setZ(p.getZ()+.1)
        start=m.center()[2]
        for _ in range(90):w.step([0.]*6)
        self.assertLess(m.center()[2],start-.05)
        self.assertGreater(m.center()[2],.012)
        self.assertTrue(all(math.isfinite(v) for p in w.nodes for v in p))

    def test_obstacles_have_physical_contacts(self):
        cfg=read_config();cfg['environment']=[dict(kind='obstacle',x=.32,y=0,radius=.04,height=.2)]
        w=World(cfg);contact=False
        for _ in range(120):
            w.mechanics.body.node().applyCentralForce(Vec3(1.5,0,0))
            w.step([0.]*6);contact |= w.blocked
        self.assertTrue(contact)
        self.assertLess(w.mechanics.head_position()[0],.32)

    def test_all_motor_axes_are_active(self):
        for axis in range(6):
            w=self.world();command=[0.]*6;command[axis]=.3
            for _ in range(30):w.step(command)
            self.assertGreater(w.angles[axis],.1,axis)

    def test_invalid_physics_and_grips(self):
        w=self.world()
        with self.assertRaises(ValueError):w.step([0.]*6,manual_grips=(1.1,0.))
        cfg=read_config();cfg['physics']['mass_kg']=-1
        with self.assertRaises(ValueError):World(cfg)


if __name__=='__main__':unittest.main()
