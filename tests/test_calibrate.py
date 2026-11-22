import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from plotledger import archive, cli
from plotledger.common import decode, encode
from plotledger.exactlp import InfeasibleError, UnboundedError, minimize
from plotledger.workflow import materialize

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / 'examples'


def bundle(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


def density(result, margin, name, cover, statistic):
    for entry in result['densities']:
        if (entry['margin'] == margin and entry.get(margin) == name
                and entry['cover'] == cover and entry['statistic'] == statistic):
            return entry
    raise AssertionError(f'missing density: {margin}/{name}/{cover}/{statistic}')


class ExactLpTest(unittest.TestCase):
    def test_bounded_minimum(self):
        # min 2x + y with x + y >= 2, x <= 1, x,y >= 0 -> (0, 2), value 2
        value, sol = minimize([Fraction(2), Fraction(1)],
                              [([1, 1], '>=', 2), ([1, 0], '<=', 1)], 2)
        self.assertEqual(value, 2)
        self.assertEqual(sol, [Fraction(0), Fraction(2)])

    def test_exact_fraction(self):
        # min x with 3x >= 1 -> 1/3
        value, sol = minimize([Fraction(1)], [([3], '>=', 1)], 1)
        self.assertEqual(value, Fraction(1, 3))
        self.assertEqual(sol, [Fraction(1, 3)])

    def test_infeasible(self):
        with self.assertRaises(InfeasibleError):
            minimize([Fraction(1)], [([1], '<=', 1), ([1], '>=', 3)], 1)

    def test_unbounded(self):
        with self.assertRaises(UnboundedError):
            minimize([Fraction(-1)], [([1], '>=', 1)], 1)

    def test_zero_control_forces_infeasibility(self):
        # x >= 1 (shifted lower bound) cannot meet a zero equality.
        with self.assertRaises(InfeasibleError):
            minimize([Fraction(0)], [([1], '=', -1)], 1)


class BaselineRegressionTest(unittest.TestCase):
    """The calibration work must not disturb the frozen baseline product."""

    def test_independent_check_values(self):
        result = materialize(bundle('baseline.json'))
        north_pine = next(r for r in result['districts']
                          if r['district'] == 'North' and r['cover'] == 'pine')
        self.assertEqual(north_pine['design_ha'], 11)
        self.assertEqual(north_pine['visited_ha'], 5)
        self.assertEqual(north_pine['basal_pi_m2_per_visited_ha'], '1/4')
        self.assertEqual(north_pine['trees_per_visited_ha'], 40)
        south = [r for r in result['districts'] if r['district'] == 'South']
        self.assertTrue(all(r['trees_per_visited_ha'] is None for r in south))

    def test_invalid_bundle_rejected(self):
        with self.assertRaises(ValueError):
            materialize(bundle('invalid.json'))


class CalibratedExampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = materialize(bundle('calibrated.json'))

    def test_joint_feasible(self):
        self.assertEqual(self.result['outcome'], 'feasible')
        self.assertFalse(self.result['separately_feasible_jointly_infeasible'])
        for system in ('zone_margins', 'district_margins', 'joint'):
            self.assertEqual(self.result['feasibility'][system]['status'], 'feasible')

    def test_certificate_weights(self):
        self.assertEqual(self.result['weights'],
                         {'p1': 1, 'p2': '3/2', 'p3': 1, 'p4': '5/4', 'p5': 1})

    def test_density_intervals_and_thresholds(self):
        entry = density(self.result, 'zone', 'Z2', 'pine', 'trees_per_ha')
        self.assertEqual(entry['interval'], ['625/17', '800/17'])
        self.assertEqual(entry['relation'], 'below')
        self.assertTrue(entry['determinate'])
        entry = density(self.result, 'district', 'South', 'pine', 'trees_per_ha')
        self.assertEqual(entry['interval'], ['425/14', '300/7'])
        entry = density(self.result, 'district', 'North', 'pine', 'trees_per_ha')
        self.assertEqual(entry['interval'], ['100/3', '100/3'])
        self.assertEqual(entry['relation'], 'above')
        entry = density(self.result, 'district', 'North', 'pine', 'basal_pi_m2_per_ha')
        self.assertEqual(entry['interval'], ['5/24', '5/24'])
        self.assertEqual(entry['relation'], 'below')

    def test_visit_statistics_travel_with_controls(self):
        cell = next(c for c in self.result['cells']
                    if c['margin'] == 'zone' and c['zone'] == 'Z2' and c['cover'] == 'mixed')
        self.assertEqual(cell['control_ha'], 8)
        self.assertEqual(cell['design_ha'], '19/2')
        self.assertEqual(cell['visited_base_ha'], '13/2')
        self.assertEqual([s['plot'] for s in cell['support']], ['p1', 'p4'])


class StraddleExampleTest(unittest.TestCase):
    def test_interval_crossing_business_line_is_flagged(self):
        result = materialize(bundle('straddle.json'))
        self.assertEqual(result['outcome'], 'feasible')
        self.assertEqual(result['weights'], {'s1': '7/4', 's2': 1})
        entry = density(result, 'zone', 'Z1', 'pine', 'trees_per_ha')
        self.assertEqual(entry['interval'], ['250/9', '350/9'])
        self.assertEqual(entry['threshold'], 30)
        self.assertEqual(entry['relation'], 'straddles')
        self.assertFalse(entry['determinate'])
        # The certificate alone would report a point above the line.
        self.assertEqual(entry['at_certificate'], '350/9')


class UnsupportedExampleTest(unittest.TestCase):
    def test_no_visit_support_classification(self):
        result = materialize(bundle('unsupported.json'))
        self.assertEqual(result['outcome'], 'infeasible')
        self.assertIsNone(result['weights'])
        joint = result['feasibility']['joint']
        self.assertEqual(joint['reason'], 'no_visit_support')
        self.assertTrue(joint['supported_part_feasible'])
        cells = {(c['margin'], c.get('zone') or c.get('district'), c['cover'])
                 for c in joint['unsupported_cells']}
        self.assertEqual(cells, {('zone', 'Z2', 'cedar'), ('district', 'South', 'cedar')})
        cell = next(c for c in result['cells'] if c.get('zone') == 'Z2')
        self.assertFalse(cell['supported'])
        self.assertEqual(cell['design_ha'], 8)


class IncompatibleExampleTest(unittest.TestCase):
    def test_bounds_block_controls(self):
        result = materialize(bundle('incompatible.json'))
        joint = result['feasibility']['joint']
        self.assertEqual(joint['reason'], 'incompatible_controls')
        self.assertTrue(joint['feasible_without_upper_bounds'])
        cell = next(c for c in result['cells'] if c.get('zone') == 'Z1')
        self.assertEqual(cell['control_ha'], 20)
        self.assertEqual(cell['max_representable_ha'], 8)


class MarginsConflictExampleTest(unittest.TestCase):
    def test_separately_feasible_jointly_infeasible(self):
        result = materialize(bundle('margins-conflict.json'))
        self.assertEqual(result['feasibility']['zone_margins']['status'], 'feasible')
        self.assertEqual(result['feasibility']['district_margins']['status'], 'feasible')
        joint = result['feasibility']['joint']
        self.assertEqual(joint['status'], 'infeasible')
        self.assertEqual(joint['reason'], 'incompatible_controls')
        self.assertFalse(joint['feasible_without_upper_bounds'])
        self.assertTrue(result['separately_feasible_jointly_infeasible'])


class ValidationTest(unittest.TestCase):
    def test_invalid_calibration_bundle_rejected(self):
        with self.assertRaises(ValueError):
            materialize(bundle('invalid-calibration.json'))

    def test_missing_control_for_supported_cell_rejected(self):
        doc = bundle('calibrated.json')
        del doc['controls']['zone_cover']['Z2']['pine']
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_threshold_requires_control_cell(self):
        doc = bundle('calibrated.json')
        doc['density_thresholds'].append({'margin': 'zone', 'zone': 'Z9', 'cover': 'pine',
                                          'statistic': 'trees_per_ha', 'threshold': 1})
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_inverted_factor_bounds_rejected(self):
        doc = bundle('calibrated.json')
        doc['plots'][0]['factor_bounds'] = [2, '1/2']
        with self.assertRaises(ValueError):
            materialize(doc)


class ArchiveRoundTripTest(unittest.TestCase):
    def test_publish_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'ledger.sqlite'
            for name in ('calibrated.json', 'margins-conflict.json', 'baseline.json'):
                doc = bundle(name)
                result = materialize(doc)
                archive.publish(db, name, doc, result)
                stored = archive.load(db, name)
                self.assertEqual(encode(stored), encode(materialize(archive.load(db, name, source=True))))
            self.assertEqual(len(archive.history(db)), 3)


class CliTest(unittest.TestCase):
    def _run(self, argv):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(argv)

    def test_preview_exit_codes(self):
        self.assertEqual(self._run(['preview', str(EXAMPLES / 'calibrated.json')]), 0)
        self.assertEqual(self._run(['preview', str(EXAMPLES / 'invalid-calibration.json')]), 2)

    def test_publish_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'ledger.sqlite'
            args = ['publish', str(EXAMPLES / 'calibrated.json'), str(db), 'ed1']
            self.assertEqual(self._run(args), 0)
            self.assertEqual(self._run(args), 2)


if __name__ == '__main__':
    unittest.main()
