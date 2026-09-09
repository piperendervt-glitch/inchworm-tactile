import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from core import World,Recorder,read_config
from layout import layout_from_config,resolve_layout,body_distance,KINDS


class LayoutTests(unittest.TestCase):
    def setUp(self):self.layout=layout_from_config(read_config())

    def random_layout(self):
        self.layout['spawn']['randomize']=True
        for g in self.layout['groups'].values():g.update(count=5,random_positions=True)
        return self.layout

    def test_reproducible_and_seed_changes(self):
        layout=self.random_layout();before=copy.deepcopy(layout)
        first=resolve_layout(layout)
        self.assertEqual(first,resolve_layout(layout));self.assertEqual(layout,before)
        layout['seed']+=1
        self.assertNotEqual(first,resolve_layout(layout))

    def test_random_bounds_and_separation(self):
        layout=self.random_layout();spawn,objects=resolve_layout(layout)
        xmin,xmax,ymin,ymax=layout['bounds']
        self.assertEqual(len(objects),15)
        for i,o in enumerate(objects):
            r=o['radius']
            self.assertTrue(xmin+r<=o['x']<=xmax-r)
            self.assertTrue(ymin+r<=o['y']<=ymax-r)
            self.assertGreater(body_distance(o['x'],o['y'],spawn),r+.03)
            for p in objects[:i]:self.assertGreater(math.hypot(o['x']-p['x'],o['y']-p['y']),r+p['radius'])

    def test_manual_positions_and_zero_count(self):
        self.layout['spawn'].update(x=1.2,y=-.3,heading_deg=90)
        for g in self.layout['groups'].values():g['count']=0
        cfg=read_config();cfg['layout']=self.layout;w=World(cfg)
        self.assertEqual(w.objects,[])
        self.assertAlmostEqual(w.nodes[-1][0],1.2)
        self.assertAlmostEqual(w.nodes[-1][1],-.03)
        self.assertEqual(w.x,1.2)

    def test_random_counts_ranges(self):
        layout=self.random_layout()
        for g in layout['groups'].values():g.update(random_count=True,count_min=2,count_max=4)
        _,objects=resolve_layout(layout)
        for k in KINDS:self.assertIn(sum(o['kind']==k for o in objects),(2,3,4))

    def test_impossible_bounds_fail(self):
        layout=self.random_layout();layout['bounds']=[0,.01,0,.01]
        with self.assertRaises(ValueError):resolve_layout(layout)

    def test_invalid_numbers_counts_and_rows(self):
        for value in (-1,101,1.5,float('nan')):
            layout=copy.deepcopy(self.layout);layout['groups']['food']['count']=value
            with self.assertRaises(ValueError):resolve_layout(layout)
        self.layout['groups']['food']['count']=10
        with self.assertRaises(ValueError):resolve_layout(self.layout)

    def test_spawn_record_and_noise_independence(self):
        cfg=read_config();cfg['layout']=self.random_layout();w=World(cfg)
        cfg['seed']=102
        other=World(cfg)
        self.assertEqual(w.objects,other.objects);self.assertEqual(w.initial_spawn,other.initial_spawn)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'log.csv';r=Recorder(path,w);r.close()
            metadata=json.loads(path.read_text().splitlines()[0][2:])
            self.assertEqual(metadata['initial_spawn'],w.initial_spawn)
            self.assertEqual(metadata['initial_objects'],w.objects)


if __name__=='__main__':unittest.main()
