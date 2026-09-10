import json
import shutil
import tempfile
import unittest
from pathlib import Path

from creature_sim import scoreboard
from creature_sim.ecoli.brain import EcoliBrain
from tests.test_replay import write_session


class ScoreboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.session = write_session(self.tmp / 'session', seconds=4.0,
                                     walk=lambda t: (-30.0 + (t * 14.0) % 60.0, 0.0),
                                     bounds=[-40, -40, 40, 40])
        self.scores = self.tmp / 'scores.json'

    def brain(self, seed, generation):
        path = self.tmp / f'gen-{generation}.json'
        EcoliBrain(seed=seed, generation=generation).to_json(path)
        return path

    def test_the_first_score_fixes_the_benchmark_and_the_start(self):
        first = scoreboard.record(self.scores, self.brain(1, 5), [self.session],
                                  creatures=3, trials=1, seconds=3.0)
        data = scoreboard.load(self.scores)
        self.assertEqual(data['benchmark']['sessions'], [str(self.session)])
        self.assertEqual(data['benchmark']['seconds'], 3.0)
        self.assertEqual(data['start'], first)
        self.assertEqual(data['latest'], first)

        # A later call's own sessions and settings are ignored.
        other = write_session(self.tmp / 'other', seconds=2.0)
        second = scoreboard.record(self.scores, self.brain(2, 6), [other],
                                   creatures=6, trials=4, seconds=None)
        data = scoreboard.load(self.scores)
        self.assertEqual(data['benchmark']['sessions'], [str(self.session)])
        self.assertEqual(data['start'], first, 'the start score must never move')
        self.assertEqual(data['latest'], second)
        self.assertEqual(len(data['history']), 2)
        self.assertEqual(data['best']['fitness'], max(first['fitness'], second['fitness']))

    def test_the_same_score_twice_is_one_entry(self):
        path = self.brain(1, 5)
        a = scoreboard.record(self.scores, path, [self.session], creatures=3, trials=1, seconds=3.0)
        b = scoreboard.record(self.scores, path)
        self.assertEqual(a['fitness'], b['fitness'], 'the benchmark should be repeatable')
        self.assertEqual(len(scoreboard.load(self.scores)['history']), 1)

    def test_a_generation_that_learned_nothing_still_gets_its_own_entry(self):
        parent = self.brain(1, 5)
        scoreboard.record(self.scores, parent, [self.session], creatures=3, trials=1, seconds=3.0)
        # Same weights, next generation number: what training writes when no candidate beat the parent.
        same = self.tmp / 'gen-6.json'
        data = json.loads(parent.read_text(encoding='utf-8'))
        data['generation'] = 6
        same.write_text(json.dumps(data), encoding='utf-8')
        scoreboard.record(self.scores, same)
        record = scoreboard.load(self.scores)
        self.assertEqual([h['generation'] for h in record['history']], [5, 6])
        self.assertEqual(record['start']['generation'], 5)
        self.assertEqual(record['latest']['generation'], 6)

    def test_no_benchmark_without_sessions(self):
        with self.assertRaises(ValueError):
            scoreboard.record(self.scores, self.brain(1, 5))

    def test_summary_lines(self):
        self.assertEqual(len(scoreboard.summary_lines({})), 1)
        start = dict(generation=5, fitness=200.0, eating_s=2.0)
        latest = dict(generation=7, fitness=300.0, eating_s=5.5)
        lines = scoreboard.summary_lines(dict(start=start, latest=latest, best=latest))
        self.assertIn('世代  5', lines[0])
        self.assertIn('+100.0', lines[1])
        self.assertIn('(+50%)', lines[1])
        self.assertIn('2.0s → 5.5s', lines[3])

    def test_a_file_written_with_a_bom_still_reads(self):
        # PowerShell 5.1 writes UTF-8 with a byte order mark.
        self.scores.write_text(json.dumps(dict(start=1)), encoding='utf-8-sig')
        self.assertEqual(scoreboard.load(self.scores), dict(start=1))


if __name__ == '__main__':
    unittest.main()
