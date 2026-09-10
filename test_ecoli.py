import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from creature_sim.ecoli.core import World,DT,config
from creature_sim.ecoli.trials import Trials,seeds_from_text

class EcoliTests(unittest.TestCase):
    def test_seed_reproducibility_layout_fairness_no_learning(self):
        a,b,c=World(17),World(17),World(18)
        weights=json.dumps([a.policy.w,a.policy.out])
        self.assertEqual(a.objects,c.objects);self.assertEqual(a.initial_pose,c.initial_pose)
        self.assertNotEqual(a.policy.fingerprint,c.policy.fingerprint)
        for _ in range(300):self.assertEqual(a.step(),b.step())
        self.assertEqual(weights,json.dumps([a.policy.w,a.policy.out]))
        self.assertGreater(a.path,0);self.assertGreater(a.turns,0)

    def test_collision_does_not_steer_or_penetrate(self):
        w=World(0,dict(objects=[dict(kind='obstacle',x=.15,y=0,radius=.05)],sensor_noise=0))
        w.policy.step=lambda inputs:0.
        for _ in range(150):w.step()
        self.assertGreater(w.contact_seconds,0)
        self.assertLessEqual(w.x,.15-.05-w.c['body_radius']+1e-8)
        self.assertEqual(w.heading,0.);self.assertEqual(w.turns,0)

    def test_food_and_harm_are_contact_only(self):
        w=World(0,dict(objects=[dict(kind='food',x=0,y=0,radius=.1)],speed=.001,initial_energy=50))
        w.step();self.assertGreater(w.food,0);self.assertLess(w.objects[0]['remaining'],30)
        w=World(0,dict(objects=[dict(kind='harm',x=.5,y=0,radius=.1)]));w.step();self.assertEqual(w.damage,0)
        w.x=.5;w.step();self.assertGreater(w.damage,0)

    def test_concentration_no_coordinates_in_policy_input(self):
        w=World(0,dict(objects=[dict(kind='food',x=.5,y=0,radius=.04)],sensor_noise=0))
        distant=w.sense_concentration();w.x=.4
        self.assertGreater(w.sense_concentration(),distant)
        w.step();self.assertEqual(len(w.last_inputs),6)
        self.assertTrue(all(0<=v<=1 for v in w.last_inputs))

    def test_fixed_duration_logs_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trials';t=Trials([0,1,2],1,{},path)
            while not t.done:t.step()
            self.assertEqual([r['seed'] for r in t.results],[0,1,2])
            self.assertTrue(all(r['elapsed_s']==1 for r in t.results))
            for seed in (0,1,2):
                with (path/f'seed-{seed}.csv').open() as f:rows=list(csv.DictReader(f))
                self.assertEqual(len(rows),30)
                self.assertEqual(float(rows[-1]['time_s']),1)
            self.assertEqual(len(json.loads((path/'summary.json').read_text())['completed']),3)
            with self.assertRaises(ValueError):Trials([0],1,{},path)

    def test_death_does_not_respawn_and_clock_finishes(self):
        w=World(0,dict(initial_energy=.001,basal_cost=1))
        w.step();pose=(w.x,w.y,w.heading)
        for _ in range(29):w.step()
        self.assertEqual(w.tick,30);self.assertEqual(w.energy,0)
        self.assertEqual((w.x,w.y,w.heading),pose);self.assertIsNotNone(w.death_time)

    def test_validation(self):
        for s in ('','1,1','-1','abc'):
            with self.assertRaises(ValueError):seeds_from_text(s)
        for c in ({'bounds':[0,0,0,0]},{'speed':0},{'obstacle_count':1.2},{'spawn':[9,9,0]}):
            with self.assertRaises(ValueError):World(0,c)
