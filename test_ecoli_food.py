import unittest
from ecoli.core import World,config

class FoodEnvironmentTests(unittest.TestCase):
    def test_food_only_random_layout_is_shared_and_reproducible(self):
        settings=dict(environment='food_only',random_spawn=True)
        a,b=World(0,settings),World(1,settings)
        self.assertEqual(a.objects,b.objects);self.assertEqual(a.initial_pose,b.initial_pose)
        self.assertEqual(len(a.objects),8);self.assertTrue(all(o['kind']=='food' for o in a.objects))
        c=World(0,dict(settings,layout_seed=8))
        self.assertNotEqual(a.initial_pose,c.initial_pose);self.assertNotEqual(a.objects,c.objects)
        for _ in range(300):a.step()
        self.assertEqual(a.damage_total,0.)

    def test_manual_hazards_are_removed_without_mutating_source(self):
        settings=dict(environment='food_only',objects=[dict(kind=k,x=.4,y=0,radius=.04) for k in ('food','harm','obstacle')])
        w=World(0,settings)
        self.assertEqual(len(w.objects),1);self.assertEqual(len(settings['objects']),3)
        self.assertEqual(w.c['harm_count'],0);self.assertEqual(w.c['obstacle_count'],0)

    def test_food_required_and_mixed_preserved(self):
        for settings in (dict(food_count=0),dict(objects=[])):
            with self.assertRaises(ValueError):World(0,dict(settings,environment='food_only'))
        w=World(0,dict(environment='mixed'))
        self.assertEqual(set(o['kind'] for o in w.objects),{'food','harm','obstacle'})

    def test_first_food_time_is_recorded_once(self):
        w=World(0,dict(environment='food_only',initial_energy=60,objects=[dict(kind='food',x=0,y=0,radius=.1)]))
        self.assertIsNone(w.first_food_time);w.step();first=w.first_food_time
        self.assertIsNotNone(first)
        for _ in range(10):w.step()
        self.assertEqual(w.first_food_time,first)
