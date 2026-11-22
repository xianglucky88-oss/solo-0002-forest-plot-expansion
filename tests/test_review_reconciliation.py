"""Acceptance tests for field re-check reconciliation.

The two certified bundles carry the same number of review stems and the same
measured diameters, but different compatible identities. Their tree counts and
basal publishability therefore diverge even though no row order or diameter
heuristic is ever consulted.
"""
import json
import sqlite3
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from plotledger.archive import load, publish
from plotledger.common import decode, scalar
from plotledger.workflow import materialize

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'


def load_example(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


def reconciliation(doc):
    return materialize(doc)['review_reconciliations'][0]


class CertifiedIdentityTests(unittest.TestCase):
    def test_same_stem_count_different_compatible_identities(self):
        merge = reconciliation(load_example('review_merge.json'))
        split = reconciliation(load_example('review_split.json'))
        self.assertEqual(merge['review_readings']['total_stems'], 3)
        self.assertEqual(split['review_readings']['total_stems'], 3)
        self.assertEqual(
            merge['basal_reconciliation']['stable_measured_basal_pi_m2_coeff'],
            split['basal_reconciliation']['stable_measured_basal_pi_m2_coeff'])

        # Both interpretations keep two trees; the merge replaces one old tree
        # while the split preserves every old anchor.
        self.assertEqual(merge['tree_reconciliation']['stable_trees'], 2)
        self.assertEqual(split['tree_reconciliation']['stable_trees'], 2)
        anchors = {tuple(component['identity'].get('old_anchors', []))
                   for component in merge['scenarios'][0]['components']}
        self.assertIn(('old-a', 'old-b'), anchors)
        split_anchors = {tuple(component['identity']['old_anchors'])
                         for component in split['scenarios'][0]['components']}
        self.assertEqual(split_anchors, {('old-a',), ('old-b',)})

    def test_duplicate_reading_of_certified_old_stem_is_not_expanded_again(self):
        merge = reconciliation(load_example('review_merge.json'))
        self.assertEqual(merge['review_readings']['duplicate'], ['s-z'])
        # Unit for this 400 m2 / 4 ha plot is 100; two trees expand to 200, never 300.
        self.assertEqual(merge['tree_reconciliation']['stable_expanded_trees'], 200)
        self.assertEqual(merge['basal_reconciliation']['stable_expanded_basal_pi_m2'],
                         scalar(100 * Fraction(37, 2000)))

    def test_tree_count_publishable_but_basal_incomplete_when_second_stem_unmeasured(self):
        split = reconciliation(load_example('review_split.json'))
        self.assertTrue(split['tree_reconciliation']['publishable'])
        self.assertEqual(split['tree_reconciliation']['stable_expanded_trees'], 200)
        self.assertFalse(split['basal_reconciliation']['publishable'])
        self.assertFalse(split['basal_reconciliation']['stable_complete'])
        self.assertEqual(split['basal_reconciliation']['missing_stems'], ['s-z'])
        self.assertEqual(split['scenarios'][0]['complete'], False)
        # The measured partial coefficient is still reported honestly, not zero or fabricated.
        self.assertEqual(split['basal_reconciliation']['stable_measured_basal_pi_m2_coeff'], '37/2000')


class AmbiguityTests(unittest.TestCase):
    def test_stable_contribution_separated_from_disputed_revisit(self):
        doc = reconciliation(load_example('review_ambiguous.json'))
        tree = doc['tree_reconciliation']
        basal = doc['basal_reconciliation']
        self.assertTrue(tree['disputed'])
        self.assertFalse(tree['publishable'])
        # old-a -> s-x is settled regardless of the chosen interpretation.
        self.assertEqual(tree['stable_trees'], 1)
        self.assertEqual(tree['stable_expanded_trees'], 100)
        self.assertEqual(tree['scenario_trees'], [2, 3])
        labels = {option['option']: option for option in tree['dispute_options']}
        self.assertEqual(labels['same-tree']['trees'], 1)
        self.assertEqual(labels['separate-tree']['trees'], 2)
        self.assertTrue(basal['disputed'])
        self.assertFalse(basal['publishable'])
        self.assertEqual(basal['stable_measured_basal_pi_m2_coeff'], '121/10000')
        # Both readings exist under either interpretation, so the dispute is not a missing slot.
        self.assertEqual(basal['missing_stems'], [])
        self.assertEqual({option['option'] for option in basal['dispute_options']},
                         {'same-tree', 'separate-tree'})
        self.assertEqual(doc['scenarios'][0]['choices'], {'g1': 'same-tree'})
        self.assertEqual(doc['scenarios'][1]['choices'], {'g1': 'separate-tree'})

    def test_identity_never_inferred_from_row_order_or_diameter_proximity(self):
        with self.assertRaisesRegex(ValueError, 'without an explicit identity statement'):
            materialize(load_example('review_invalid.json'))

    def test_indistinguishable_ambiguity_options_are_rejected(self):
        doc = load_example('review_ambiguous.json')
        group = doc['reviews'][0]['ambiguities'][0]
        group['options'][1]['statements'] = group['options'][0]['statements']
        with self.assertRaisesRegex(ValueError, 'do not distinguish any identity'):
            materialize(doc)

    def test_subminimum_measured_review_stem_is_rejected_not_silently_dropped(self):
        doc = load_example('review_split.json')
        doc['reviews'][0]['stems'][2]['dbh_cm'] = 8
        with self.assertRaisesRegex(ValueError, 'sub-minimum measured review stem'):
            materialize(doc)

    def test_certified_ambiguity_conflict_on_old_stem_is_rejected(self):
        doc = load_example('review_ambiguous.json')
        doc['reviews'][0]['ambiguities'][0]['options'][0]['statements'].append(
            {'type': 'rename', 'review_stem': 's-z', 'old_stem': 'old-a'})
        with self.assertRaisesRegex(ValueError, 'certified old stem'):
            materialize(doc)


class ArchiveTests(unittest.TestCase):
    def test_review_edition_round_trips_through_sqlite(self):
        doc = load_example('review_split.json')
        result = materialize(doc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.sqlite'
            publish(path, 'split-edition', doc, result)
            stored = load(path, 'split-edition')
            self.assertEqual(stored, result)
            with sqlite3.connect(path) as db:
                names = [row[0] for row in db.execute('SELECT name FROM editions')]
            self.assertEqual(names, ['split-edition'])
            replay = materialize(load(path, 'split-edition', source=True))
            self.assertEqual(replay, result)


if __name__ == '__main__':
    unittest.main()
