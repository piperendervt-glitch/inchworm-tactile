import unittest
import math
import copy
from core import World,read_config
from locomotion import PadMechanics,shape,normal_loads,reference_angles


class LocomotionTests(unittest.TestCase):
    def run_flat(self,strategy='directional',seconds=6,override=None,mode='reference'):
        cfg=read_config();cfg['environment']=[]
        cfg['physics'].update(override or {})
        cfg['physics']['strategy']=strategy
        w=World(cfg);states=set()
        for _ in range(round(seconds*30)):
            w.step(mode=mode);states.add(tuple(w.mechanics.states))
        return w,states

    def test_directional_support_alternates_and_advances(self):
        w,states=self.run_flat()
        self.assertIn(('stick','slip'),states)
        self.assertIn(('slip','stick'),states)
        self.assertGreater(w.x,.15)

    def test_reversed_friction_reverses_motion(self):
        w,_=self.run_flat(override={'mu_forward':1.1,'mu_backward':.16})
        self.assertLess(w.x,-.15)

    def test_symmetric_friction_no_cycle_drift(self):
        w,_=self.run_flat(override={'mu_forward':.5,'mu_backward':.5})
        self.assertAlmostEqual(w.x,0.,places=8)

    def test_force_limit_stalls(self):
        w,_=self.run_flat(override={'max_drive_n':.001})
        self.assertAlmostEqual(w.x,0.)
        w.step(mode='reference')
        self.assertEqual(w.mechanics.reason,'force limit')

    def test_active_both_gripped_holds(self):
        m=PadMechanics({'strategy':'active_grip'})
        angles,dx=m.advance([0.]*6,[-.06]*5+[0.],1/30,[True,True])
        self.assertEqual(angles,[0.]*6)
        self.assertEqual(dx,0.)
        self.assertEqual(m.reason,'force limit')

    def test_load_balance_and_geometry(self):
        for tick in range(180):
            points=shape(reference_angles(tick/30,'load_transfer')[:5])
            rear,front=normal_loads(points,1.4715)
            self.assertAlmostEqual(rear+front,1.4715)
            com=sum((points[i][0]+points[i+1][0])/2 for i in range(6))/6
            self.assertAlmostEqual(front*points[-1][0],com*1.4715)
            self.assertAlmostEqual(points[-1][1],0.)
            self.assertGreaterEqual(min(z for x,z in points),-1e-10)
            for a,b in zip(points,points[1:]):
                self.assertAlmostEqual(math.dist(a,b),.045)

    def test_load_transfer_and_active_advance(self):
        for strategy in ('load_transfer','active_grip'):
            w,_=self.run_flat(strategy)
            self.assertGreater(w.x,.1)

    def test_no_action_no_motion(self):
        cfg=read_config();cfg['environment']=[]
        w=World(cfg)
        for _ in range(180):w.step([0.]*6)
        self.assertEqual(w.x,0.)

    def test_obstacle_rejects_motion(self):
        cfg=read_config()
        cfg['environment']=[dict(kind='obstacle',x=.32,y=0.,radius=.02,height=.3)]
        w=World(cfg)
        blocked=False
        for _ in range(180):
            w.step(mode='reference');blocked |= w.blocked
            if w.blocked:self.assertEqual(w.mechanics.slip,[0.,0.])
        self.assertTrue(blocked)
        self.assertLessEqual(w.nodes[-1][0],.30+1e-8)


if __name__=='__main__':unittest.main()
