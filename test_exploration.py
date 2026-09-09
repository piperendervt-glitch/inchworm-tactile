import math
import unittest
from exploration import MotorExploration
from body_limits import ANGLE_LIMITS
from core import World,read_config,DT

class ExplorationTests(unittest.TestCase):
    def test_reproducible_independent_and_bounded_smooth_motion(self):
        a,b=MotorExploration(17),MotorExploration(17)
        self.assertNotEqual(a.goals,MotorExploration(18).goals)
        self.assertEqual(len(set(a.durations)),6)
        before=a.values[:];minimum=[0.]*6;maximum=[0.]*6
        for _ in range(1800):
            values=a.step(DT);self.assertEqual(values,b.step(DT))
            for i,v in enumerate(values):
                self.assertLessEqual(abs(v),ANGLE_LIMITS[i]+1e-9)
                self.assertLessEqual(abs(v-before[i]),1.2*DT+1e-9)
                minimum[i]=min(minimum[i],v);maximum[i]=max(maximum[i],v)
            before=values
        for i in range(6):
            self.assertLess(minimum[i],-.3*ANGLE_LIMITS[i]);self.assertGreater(maximum[i],.3*ANGLE_LIMITS[i])

    def test_no_learning_and_real_bending(self):
        w=World();weights=w.controller.weights;max_angles=[0.]*6
        for _ in range(900):
            w.step()
            for i,v in enumerate(w.angles):max_angles[i]=max(max_angles[i],abs(v))
        self.assertEqual(weights,w.controller.weights);self.assertEqual(w.controller.training_steps,0)
        for i in (1,2,4,5):self.assertGreater(max_angles[i],math.radians(25),i)
        self.assertTrue(all(math.isfinite(v) for p in w.nodes for v in p))

    def test_disabled_and_manual_bypass(self):
        cfg=read_config();cfg['exploration']['enabled']=False;w=World(cfg);w.step()
        self.assertEqual(w.targets,w.network_targets);self.assertEqual(w.exploration.steps,0)
        w=World();w.step([0.]*6)
        self.assertEqual(w.exploration.steps,0);self.assertEqual(w.targets,[0.]*6)

    def test_start_is_smooth_and_bad_settings_rejected(self):
        e=MotorExploration(17);self.assertLess(max(map(abs,e.step(DT))),.001)
        for setting in ({'weight':2},{'min_seconds':0},{'enabled':1},{'max_seconds':.1},{'amplitude':float('nan')}):
            with self.assertRaises(ValueError):MotorExploration(17,setting)
