"""End-to-end tests for the `revisit` CLI against a sealed SQLite ledger."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from plotledger.archive import publish
from plotledger.cli import main
from plotledger.common import decode, encode
from plotledger.workflow import materialize


def plot(pid, expansion, cover='pine'):
    return {'id': pid, 'district': 'North', 'plot_m2': 400,
            'expansion_ha': expansion,
            'segments': [{'id': f'{pid}a', 'cover': cover, 'fraction': 1}]}


def visit(vid, pid='p1', status='visited', trees=()):
    return {'id': vid, 'plot': pid, 'cover': 'pine', 'status': status,
            'trees': [{'id': t, 'dbh_cm': d} for t, d in trees]}


class CliRevisitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ledger = self.tmp / 'ledger.sqlite'
        self.old = {'survey': 'e2e', 'min_dbh_cm': 10,
                    'plots': [plot('p1', 4)],
                    'visits': [visit('v1', trees=[('a', 20), ('b', 30)])]}
        self.new = {'survey': 'e2e', 'min_dbh_cm': 10,
                    'plots': [plot('p1', 4)],
                    'visits': [visit('v1', trees=[('a', 21), ('b', 31)])]}
        publish(self.ledger, 'original', self.old, materialize(self.old))
        publish(self.ledger, 'revised', self.new, materialize(self.new))
        self.candidates = self.tmp / 'candidates.json'
        self.candidates.write_text(encode({
            'survey': 'e2e', 'visit_candidates': [], 'tree_candidates': [
                {'id': 'c1', 'old_visit': 'v1', 'old_tree': 'a',
                 'new_visit': 'v1', 'new_tree': 'b'},
                {'id': 'c2', 'old_visit': 'v1', 'old_tree': 'b',
                 'new_visit': 'v1', 'new_tree': 'a'},
            ]}), encoding='utf-8')
        self.field = self.tmp / 'field.json'
        self.stats = self.tmp / 'stats.json'

    def run_revisit(self):
        argv = ['revisit', str(self.ledger), 'original', 'revised',
                str(self.candidates), '--field', str(self.field),
                '--stats', str(self.stats)]
        rc = main(argv)
        self.assertEqual(rc, 0)
        return decode(self.field.read_text(encoding='utf-8')), \
            decode(self.stats.read_text(encoding='utf-8'))

    def test_full_flow_writes_two_packets_and_manifest(self):
        field, stats = self.run_revisit()
        self.assertEqual(field['packet'], 'field_revisit_queue')
        self.assertEqual(stats['packet'], 'statistician_review')
        self.assertEqual(len(field['tasks']), 1)
        # Mutual cross-check ids line up.
        self.assertEqual([t['task_id'] for t in field['tasks']],
                         stats['cross_check']['field_task_ids'])
        # Field packet contains no area/weight vocabulary.
        text = self.field.read_text(encoding='utf-8')
        for banned in ('ha', 'weight', 'expansion', 'density'):
            self.assertNotIn(banned, text)

    def test_existing_outputs_are_not_overwritten(self):
        self.run_revisit()
        rc = main(['revisit', str(self.ledger), 'original', 'revised',
                   str(self.candidates), '--field', str(self.field),
                   '--stats', str(self.tmp / 'other.json')])
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / 'other.json').exists())

    def test_tampered_published_result_blocks_queue(self):
        db = sqlite3.connect(self.ledger)
        doc = decode(db.execute("SELECT result FROM editions WHERE name='revised'").fetchone()[0])
        doc['districts'][0]['visited_ha'] = 999
        db.execute("UPDATE editions SET result=? WHERE name='revised'", (encode(doc),))
        db.commit()
        db.close()
        rc = main(['revisit', str(self.ledger), 'original', 'revised',
                   str(self.candidates), '--field', str(self.tmp / 'f.json'),
                   '--stats', str(self.tmp / 's.json')])
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / 'f.json').exists())

    def test_invalid_candidate_exits_2_and_writes_nothing(self):
        self.candidates.write_text(encode({
            'survey': 'e2e', 'tree_candidates': [
                {'id': 'c1', 'old_visit': 'v1', 'old_tree': 'ghost',
                 'new_visit': 'v1', 'new_tree': 'a'}]}), encoding='utf-8')
        rc = main(['revisit', str(self.ledger), 'original', 'revised',
                   str(self.candidates), '--field', str(self.field),
                   '--stats', str(self.stats)])
        self.assertEqual(rc, 2)
        self.assertFalse(self.field.exists())
        self.assertFalse(self.stats.exists())


if __name__ == '__main__':
    unittest.main()
