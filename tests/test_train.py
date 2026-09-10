import json
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim.ecoli.brain import EcoliBrain
from creature_sim.evaluate import measure
from creature_sim.train import Trainer, collect, flatten, save, unflatten
from tests.test_replay import write_session


class WeightVectorTests(unittest.TestCase):
    def test_flatten_and_unflatten_are_inverses(self):
        brain = EcoliBrain(seed=4)
        values = flatten(brain)
        # Eight state rows of 112 (104 inputs + 8 state), plus two output rows of 8.
        self.assertEqual(len(values), 8 * 112 + 2 * 8)

        same = unflatten(values, brain)
        self.assertEqual(same.fingerprint, brain.fingerprint)
        self.assertEqual(same.w, brain.w)
        self.assertEqual(same.out, brain.out)

    def test_a_changed_weight_changes_the_fingerprint(self):
        brain = EcoliBrain(seed=4)
        values = flatten(brain)
        values[17] += 1.0
        self.assertNotEqual(unflatten(values, brain).fingerprint, brain.fingerprint)


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # A pilot who sweeps back and forth, so contact is possible and the
        # score has something to climb.
        self.session = write_session(
            self.tmp / 'session', seconds=6.0,
            walk=lambda t: (-30.0 + (t * 14.0) % 60.0, 0.0),
            bounds=[-40, -40, 40, 40])

    def train(self, generations=2, **kw):
        options = dict(population=6, elite=2, sigma=0.15, creatures=3, trials=1,
                       workers=1, quiet=True)
        options.update(kw)
        trainer = Trainer([self.session], brain=EcoliBrain(seed=1), **options)
        learned = trainer.run(generations)
        return trainer, learned

    def test_two_generations_never_make_it_worse(self):
        trainer, learned = self.train(generations=2)

        self.assertEqual(len(trainer.history), 3)
        bests = [h['best'] for h in trainer.history]
        for earlier, later in zip(bests, bests[1:]):
            self.assertGreaterEqual(later, earlier,
                                    f'the best score went down: {bests}')
        # The history is rounded to four places; the trainer keeps the full value.
        self.assertAlmostEqual(trainer.best_score, bests[-1], places=4)
        # The best of the run is carried forward, so it cannot be lost.
        self.assertGreaterEqual(trainer.best_score, bests[0])

    def test_the_saved_file_carries_weights_that_match_its_fingerprint(self):
        trainer, learned = self.train(generations=2)
        path = save(learned, self.tmp / 'gen-001.json', trainer,
                    parent='brains/ecoli-gen-000.json')

        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['generation'], 1)
        self.assertEqual(data['inputs'], 104)
        self.assertFalse(data['training_enabled'],
                         'a saved brain must still say it does not learn in play')

        # Reloading checks the fingerprint against the weights.
        reloaded = EcoliBrain.from_json(str(path))
        self.assertEqual(reloaded.fingerprint, data['fingerprint'])
        self.assertEqual(reloaded.generation, 1)

        record = data['training']
        self.assertEqual(record['parent'], 'brains/ecoli-gen-000.json')
        self.assertEqual(len(record['history']), 3)
        self.assertEqual(record['sessions'], [str(self.session)])
        self.assertAlmostEqual(record['best_fitness'], round(trainer.best_score, 4))

        # A tampered file is refused rather than quietly loaded.
        data['w'][0][0] += 1.0
        broken = self.tmp / 'broken.json'
        broken.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            EcoliBrain.from_json(str(broken))

    def test_the_generation_number_moves_on(self):
        trainer = Trainer([self.session], brain=EcoliBrain(seed=1, generation=3),
                          population=4, elite=1, creatures=2, trials=1,
                          workers=1, quiet=True)
        learned = trainer.run(1)
        self.assertEqual(learned.generation, 4)

    def test_evaluate_reports_the_same_score_the_trainer_saw(self):
        trainer, learned = self.train(generations=1)
        stats = measure(trainer.brain, [self.session], creatures=3, trials=1)
        self.assertAlmostEqual(stats['fitness'], trainer.history[0]['best'], places=3)
        self.assertEqual(stats['runs'], 1)

    def test_collect_skips_directories_that_hold_no_run(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        (empty / 'link.jsonl').write_text('', encoding='utf-8')
        found = collect([str(self.tmp / '*')])
        self.assertEqual([Path(p).name for p in found], ['session'])


if __name__ == '__main__':
    unittest.main()
