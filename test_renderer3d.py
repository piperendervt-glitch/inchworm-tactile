import copy
import importlib.util
import unittest
from core import World


@unittest.skipUnless(importlib.util.find_spec('panda3d') and importlib.util.find_spec('PIL'),'3D dependencies not installed')
class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from renderer3d import Renderer3D
        cls.renderer=Renderer3D(480,240)

    @classmethod
    def tearDownClass(cls):cls.renderer.close()

    def test_observer_does_not_change_simulation(self):
        world=World()
        for _ in range(20):world.step()
        state=copy.deepcopy((world.nodes,world.belly,world.head,world.controller.state,world.tick,world.hp,world.objects))
        self.renderer.render(world)
        self.assertEqual(state,(world.nodes,world.belly,world.head,world.controller.state,world.tick,world.hp,world.objects))

    def test_orbit_changes_image(self):
        world=World();self.renderer.home()
        self.renderer.render(world)
        before=self.renderer.render(world)
        self.renderer.orbit(120,30)
        after=self.renderer.render(world)
        self.assertEqual(after.size,(480,240))
        self.assertNotEqual(before.tobytes(),after.tobytes())
        self.assertGreater(len(set(after.get_flattened_data())),50)

    def test_eaten_objects_and_reset(self):
        world=World();self.renderer.render(world)
        world.objects[0]['eaten']=True
        self.renderer.render(world)
        self.assertTrue(self.renderer.objects[0].isHidden())
        self.renderer.render(World())
        self.assertFalse(self.renderer.objects[0].isHidden())

    def test_zoom_limits(self):
        self.renderer.zoom(1000);self.assertEqual(self.renderer.distance,.22)
        self.renderer.zoom(-1000);self.assertEqual(self.renderer.distance,5.)
        self.renderer.home()


if __name__=='__main__':unittest.main()
