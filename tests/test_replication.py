import json
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plotledger.common import encode, sqrt_text
from plotledger.workflow import materialize

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'


def bundle(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


def by_design(result):
    return {design['id']: design for design in result['replications']}


class BaselineCompatibility(unittest.TestCase):
    def test_baseline_matches_published_reference(self):
        result = materialize(bundle('baseline.json'))
        pine = next(d for d in result['districts'] if d['district'] == 'North' and d['cover'] == 'pine')
        self.assertEqual(pine['design_ha'], 11)
        self.assertEqual(pine['visited_ha'], 5)
        self.assertEqual(pine['basal_pi_m2_per_visited_ha'], '1/4')
        self.assertEqual(pine['trees_per_visited_ha'], 40)
        south = [d for d in result['districts'] if d['district'] == 'South']
        self.assertTrue(all(d['trees_per_visited_ha'] is None for d in south))
        self.assertNotIn('replications', result)

    def test_changed_is_rejected_only_when_counts_do_not_add(self):
        result = materialize(bundle('changed.json'))
        self.assertNotIn('replications', result)
        self.assertEqual(len(result['districts']), 4)


class ReplicateNumerators(unittest.TestCase):
    def setUp(self):
        self.result = materialize(bundle('replication.json'))
        self.designs = by_design(self.result)

    def test_each_replicate_has_its_own_denominator(self):
        domain = next(d for d in self.designs['concordant']['domains']
                      if (d['district'], d['cover']) == ('North', 'pine'))
        for row in domain['replicates']:
            self.assertEqual(row['visited_ha'], 6)
            # numerator and denominator are both recomputed for the group
            self.assertEqual(Fraction(row['trees_per_visited_ha']),
                             Fraction(row['expanded_trees']) / Fraction(row['visited_ha']))

    def test_full_estimate_matches_published_districts(self):
        published = {(d['district'], d['cover']): d['trees_per_visited_ha']
                     for d in self.result['districts']}
        for domain in self.designs['concordant']['domains']:
            key = (domain['district'], domain['cover'])
            self.assertEqual(domain['full']['trees_per_visited_ha'], published[key])

    def test_same_marginals_flip_only_the_within_group_linkage(self):
        concordant, discordant = self.designs['concordant'], self.designs['discordant']
        coefficients = lambda design: sorted(r['coefficient'] for r in design['replicates'])
        self.assertEqual(coefficients(concordant), coefficients(discordant))
        for a, b in zip(concordant['domains'], discordant['domains']):
            self.assertEqual(a['district'], b['district'])
            self.assertEqual(a['cover'], b['cover'])
            self.assertEqual(a['full'], b['full'])
            # The marginal replicate distribution is identical: the same
            # multiset of replicate densities, only re-associated with groups.
            self.assertEqual(sorted((_optional(r['trees_per_visited_ha']) for r in a['replicates']),
                                    key=_sort_key),
                             sorted((_optional(r['trees_per_visited_ha']) for r in b['replicates']),
                                    key=_sort_key))
            self.assertEqual(a['variance'], b['variance'])
        # ... while the paired contrast variance differs because the sign of
        # the within-group difference flips.
        a = concordant['contrasts'][0]
        b = discordant['contrasts'][0]
        self.assertEqual(a['full'], b['full'])
        self.assertNotEqual(a['variance'], b['variance'])
        self.assertGreater(_fraction(b['variance']['trees_per_visited_ha']),
                           _fraction(a['variance']['trees_per_visited_ha']))
        # Opposite linkage shows up as opposite-signed replicate differences.
        self.assertGreater(len({_fraction(row['trees_per_visited_ha'])
                                for row in a['replicate_differences']}), 0)
        self.assertTrue(any(_fraction(row['trees_per_visited_ha']) < 0
                            for row in b['replicate_differences']))

    def test_contrast_full_estimate_is_the_marginal_difference(self):
        for design in self.designs.values():
            domains = {(d['district'], d['cover']): d for d in design['domains']}
            for contrast in design['contrasts']:
                first, second = contrast['covers']
                district = contrast['district']
                left = domains[district, first]['full']['trees_per_visited_ha']
                right = domains[district, second]['full']['trees_per_visited_ha']
                if not contrast['full']['supported']:
                    self.assertIsNone(contrast['full']['trees_per_visited_ha'])
                    continue
                self.assertEqual(Fraction(contrast['full']['trees_per_visited_ha']),
                                 Fraction(left) - Fraction(right))

    def test_contrast_uses_one_replicate_number_at_a_time(self):
        contrast = self.designs['discordant']['contrasts'][0]
        domains = {(d['district'], d['cover']): d for d in self.designs['discordant']['domains']}
        for row in contrast['replicate_differences']:
            pine = next(r for r in domains['North', 'pine']['replicates'] if r['id'] == row['id'])
            mixed = next(r for r in domains['North', 'mixed']['replicates'] if r['id'] == row['id'])
            self.assertEqual(Fraction(row['trees_per_visited_ha']),
                             Fraction(pine['trees_per_visited_ha'])
                             - Fraction(mixed['trees_per_visited_ha']))
            self.assertEqual(Fraction(row['basal_pi_m2_per_visited_ha']),
                             Fraction(pine['basal_pi_m2_per_visited_ha'])
                             - Fraction(mixed['basal_pi_m2_per_visited_ha']))


class PublishabilitySplit(unittest.TestCase):
    def setUp(self):
        self.designs = by_design(materialize(bundle('replication.json')))

    def test_split_support_withholds_contrast_but_publishes_estimates(self):
        design = self.designs['split-support']
        contrast = design['contrasts'][0]
        self.assertEqual(contrast['status'], 'insufficient_common_replicates')
        self.assertIsNone(contrast['variance'])
        self.assertEqual(contrast['common_replicates'], [])
        self.assertEqual(contrast['unpaired_replicates']['only_first_cover'], ['g1', 'g2'])
        self.assertEqual(contrast['unpaired_replicates']['only_second_cover'], ['g3', 'g4'])
        self.assertEqual(design['release']['publishable_contrasts'], [])
        self.assertIn('pine-minus-mixed', [c['id'] for c in design['release']['withheld_contrasts']])
        north = [d for d in design['release']['publishable_domain_estimates']
                 if d['district'] == 'North']
        self.assertEqual(len(north), 2)

    def test_unpaired_groups_are_never_mixed_into_one_contrast(self):
        design = self.designs['split-support']
        contrast = design['contrasts'][0]
        seen = set(contrast['unpaired_replicates']['only_first_cover'])
        seen |= set(contrast['unpaired_replicates']['only_second_cover'])
        self.assertTrue(seen.isdisjoint(contrast['common_replicates']))
        self.assertEqual(len(seen), contrast['declared_replicate_count'])

    def test_marginal_estimates_still_publishable_when_contrast_is_not(self):
        design = self.designs['split-support']
        for domain in design['domains']:
            if domain['district'] != 'North':
                continue
            self.assertEqual(domain['status'], 'publishable')
            self.assertEqual(domain['uncertainty_status'], 'publishable_partial_replicates')
            # Declared coefficients are not renormalized onto the usable half.
            self.assertEqual(domain['usable_coefficient_sum'], '1/2')
            self.assertEqual(domain['declared_coefficient_sum'], 1)


class SimplerChecks(unittest.TestCase):
    def test_sqrt_text_is_exact_for_perfect_squares(self):
        self.assertEqual(sqrt_text('625/9'), '25/3')
        self.assertEqual(sqrt_text(0), '0')
        self.assertTrue(sqrt_text(2).startswith('1.4142135623'))

    def test_reference_outputs_are_unchanged(self):
        result = materialize(bundle('baseline.json'))
        self.assertEqual(encode(result), encode(materialize(bundle('baseline.json'))))


def _fraction(value):
    return Fraction(value)


def _optional(value):
    return None if value is None else Fraction(value)


def _sort_key(value):
    return (value is None, value or 0)


class CommandLine(unittest.TestCase):
    def test_replication_bundle_previews_without_archive(self):
        run = subprocess.run([sys.executable, '-m', 'plotledger', 'preview',
                              str(EXAMPLES / 'replication.json')],
                             cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        output = json.loads(run.stdout)
        self.assertEqual([d['id'] for d in output['replications']],
                         ['concordant', 'discordant', 'split-support'])

    def test_invalid_bundle_still_fails(self):
        run = subprocess.run([sys.executable, '-m', 'plotledger', 'preview',
                              str(EXAMPLES / 'invalid.json')],
                             cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 2)

    def test_publish_and_replay_keep_replicates(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = str(Path(folder) / 'ledger.sqlite')
            publish = subprocess.run([sys.executable, '-m', 'plotledger', 'publish',
                                      str(EXAMPLES / 'replication.json'), archive, 'v1'],
                                     cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(publish.returncode, 0, publish.stderr)
            replay = subprocess.run([sys.executable, '-m', 'plotledger', 'replay', archive, 'v1'],
                                    cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            shown = json.loads(subprocess.run(
                [sys.executable, '-m', 'plotledger', 'show', archive, 'v1'],
                cwd=ROOT, capture_output=True, text=True, check=True).stdout)
            self.assertEqual(len(shown['replications']), 3)
            self.assertEqual(shown['replications'][2]['contrasts'][0]['status'],
                             'insufficient_common_replicates')


if __name__ == '__main__':
    unittest.main()
