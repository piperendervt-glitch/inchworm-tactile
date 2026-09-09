import copy
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest
from core import World,Recorder,read_config
from newborn import NewbornController,Observation
from locomotion import PadMechanics,shape,normal_loads


class NewbornTests(unittest.TestCase):
    def test_frozen_reproducible_birth(self):
        a,b=World(),World();weights=a.controller.weights;digest=a.controller.fingerprint
        for _ in range(300):self.assertEqual(a.step(),b.step())
        self.assertEqual(weights,a.controller.weights);self.assertEqual(digest,a.controller.fingerprint)
        self.assertEqual(a.controller.training_steps,0)
        self.assertTrue(all(0<=v<=1 for v in a.observation().flatten()))

    def test_internal_inputs_and_training_guard(self):
        base=World().observation()
        obs=Observation(base.belly,base.head,base.joint_touch,base.support_touch,.7,.02,.8,.3)
        controller=NewbornController();controller.step(obs)
        for inputs in controller.local_inputs:self.assertEqual(inputs[29:33],[.7,.02,.8,.3])
        cfg=read_config();cfg['controller']['training_enabled']=True
        with self.assertRaises(ValueError):World(cfg)

    def test_birth_seed_changes_initial_network(self):
        self.assertNotEqual(NewbornController(17).weights,NewbornController(18).weights)
        self.assertEqual(NewbornController(17).state,[[0.]*4 for _ in range(5)])

    def test_reference_mode_removed(self):
        with self.assertRaises(ValueError):World().step(mode='reference')
        import locomotion
        self.assertFalse(hasattr(locomotion,'reference_angles'))

    def test_locality(self):
        a,b=NewbornController(),NewbornController()
        w=World();obs=w.observation()
        changed=Observation(obs.belly[:-9]+(1.,)*9,(1.,)*9,obs.joint_touch,obs.support_touch,obs.hp,obs.damage_hp,obs.hunger,obs.food_relief)
        a.step(obs);b.step(changed)
        self.assertEqual(a.state[:4],b.state[:4]);self.assertNotEqual(a.state[4],b.state[4])

    def test_position_is_not_an_input(self):
        a=World();cfg=read_config();cfg['spawn']={'x':5.,'y':2.,'heading_deg':90}
        b=World(cfg)
        self.assertEqual(a.observation(),b.observation())
        self.assertEqual(a.controller.step(a.observation()),b.controller.step(b.observation()))

    def test_joint_touch_is_delayed_and_directional(self):
        cfg=read_config();cfg['sensor']['noise']=0.;cfg['environment']=[]
        w=World(cfg);baseline=w.joint_touch[:]
        w.angles[:5]=[-.3]*5;w.nodes=w.geometry()
        w.sense();w.sense();self.assertEqual(w.joint_touch,baseline)
        w.sense()
        self.assertGreater(w.joint_touch[1],w.joint_touch[0])
        self.assertGreater(max(w.support_touch),cfg['sensor']['baseline'])

    def test_hunger_food_damage_and_death(self):
        cfg=read_config();cfg['environment']=[dict(kind='food',x=.284,y=0,radius=.06,height=.04)]
        w=World(cfg);w.hunger=70.;w.hp=50.
        recovered=[]
        for _ in range(35):
            w.step([0.]*6);recovered.append(w.food_relief)
        self.assertEqual(w.food_events,1);self.assertAlmostEqual(sum(recovered),40.)
        self.assertLess(w.hunger,31.);self.assertLess(w.hp,50.)
        self.assertEqual(w.damage_hp,0.)
        cfg['environment'][0]['kind']='harm';w=World(cfg);w.step([0.]*6)
        self.assertAlmostEqual(w.damage_hp,.4);self.assertEqual(w.food_relief,0.)
        w.hp=.01;w.step([0.]*6);self.assertEqual(w.hp,0.);self.assertIsNone(w.step())

    def test_starvation_increases_hp_loss(self):
        cfg=read_config();cfg['environment']=[]
        a,b=World(cfg),World(cfg);b.hunger=100.
        a.step([0.]*6);b.step([0.]*6)
        self.assertLess(b.hp,a.hp);self.assertEqual(b.damage_hp,0.)

    def test_csv_contains_actual_policy_inputs_and_continuous_grips(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'episode.csv';w=World();obs=w.observation().flatten()
            recorder=Recorder(path,w);recorder.write(w.step());recorder.close()
            with path.open() as file:
                meta=json.loads(file.readline()[2:]);rows=list(csv.reader(file))
            self.assertEqual(meta['schema'],3);self.assertFalse(meta['training_enabled'])
            self.assertEqual(len(rows[0]),len(rows[1]));self.assertEqual(list(map(float,rows[1][2:88])),obs)
            self.assertAlmostEqual(float(rows[1][rows[0].index('rear_grip')]),w.controller.grips[0])

    def test_static_input_converges_without_a_clock(self):
        controller=NewbornController();obs=World().observation()
        for _ in range(200):controller.step(obs)
        before=controller.state[:];controller.step(obs)
        self.assertLess(max(abs(x-y) for a,b in zip(before,controller.state) for x,y in zip(a,b)),1e-10)


class PhysicsTests(unittest.TestCase):
    def test_continuous_support_changes_friction_capacity(self):
        values=[]
        for grip in (0.,.5,1.):
            m=PadMechanics();m.advance([0.]*6,[0.]*6,1/30,[grip,grip]);values.append(m.capacity[0])
        self.assertLess(values[0],values[1]);self.assertLess(values[1],values[2])

    def test_support_limits_and_symmetry(self):
        m=PadMechanics();old=[0.]*6;candidate=[-.06]*5+[0.]
        actual,dx=m.advance(old,candidate,1/30,[1.,1.])
        self.assertEqual(actual,old);self.assertEqual(dx,0.)
        actual,dx=m.advance(old,candidate,1/30,[0.,0.])
        self.assertAlmostEqual(dx+(shape(candidate[:5])[-1][0]-shape(old[:5])[-1][0])/2,0.)
        with self.assertRaises(ValueError):m.advance(old,candidate,1/30,[1.2,0.])

    def test_geometric_and_load_balance(self):
        points=shape([-.1,-.2,-.3,-.2,-.1]);rear,front=normal_loads(points,1.4715)
        self.assertAlmostEqual(rear+front,1.4715)
        com=sum((points[i][0]+points[i+1][0])/2 for i in range(6))/6
        self.assertAlmostEqual(front*points[-1][0],com*1.4715)
        self.assertAlmostEqual(points[-1][1],0.)
        for a,b in zip(points,points[1:]):self.assertAlmostEqual(math.dist(a,b),.045)


if __name__=='__main__':unittest.main()
