"""Invariant tests for cross-edition revisit reconciliation.

Run with: python3 -m unittest discover -s tests -v
"""
import copy
import unittest

from plotledger.common import encode
from plotledger.reconcile import (
    FORBIDDEN_FIELD_KEYS, Editions, build_components,
    reconcile, resolve_visit_identity, validate_candidates, analyse_components,
)


def plot(pid, district='North', expansion=4, area=400, cover='pine', fraction=1):
    return {'id': pid, 'district': district, 'plot_m2': area,
            'expansion_ha': expansion,
            'segments': [{'id': f'{pid}a', 'cover': cover, 'fraction': fraction}]}


def visit(vid, plot='p1', cover='pine', status='visited', trees=()):
    return {'id': vid, 'plot': plot, 'cover': cover, 'status': status,
            'trees': [{'id': t, 'dbh_cm': d} for t, d in trees]}


def bundle(plots, visits):
    return {'survey': 'unit', 'min_dbh_cm': 10, 'plots': plots, 'visits': visits}


def overlay(visit_candidates=(), tree_candidates=()):
    return {'survey': 'unit', 'editions': ['old', 'new'],
            'visit_candidates': list(visit_candidates),
            'tree_candidates': list(tree_candidates)}


def tree_cid(cid, old_visit, old_tree, new_visit, new_tree):
    return {'id': cid, 'old_visit': old_visit, 'old_tree': old_tree,
            'new_visit': new_visit, 'new_tree': new_tree}


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        # One cell p1/pine with two trees in both editions (stable ids).
        self.old = bundle([plot('p1')], [visit('v1', trees=[('a', 20), ('b', 30)])])
        self.new = bundle([plot('p1')], [visit('v1', trees=[('a', 21), ('b', 31)])])
        self.editions = Editions(self.old, self.new)

    def test_proven_identity_is_not_redispatched(self):
        candidates = overlay()
        field, stats = reconcile(self.old, self.new, candidates, [])
        self.assertEqual(field['tasks'], [])
        self.assertEqual(stats['identity']['deduped_without_redispatch']['proven_visits'], 1)
        self.assertEqual(stats['identity']['deduped_without_redispatch']['proven_trees'], 2)
        self.assertEqual(stats['blocking_disputes'], [])

    def test_one_new_record_cannot_confirm_two_old_trees(self):
        # Candidate c2 claims new tree b is also old tree a: new b then has two
        # claims (proven b->b plus candidate a->b), and old a has two.
        candidates = overlay(tree_candidates=[
            tree_cid('c1', 'v1', 'a', 'v1', 'b'),
            tree_cid('c2', 'v1', 'b', 'v1', 'a'),
        ])
        field, stats = reconcile(self.old, self.new, candidates, [])
        disputes = stats['blocking_disputes']
        self.assertEqual(len(disputes), 1)
        self.assertTrue(disputes[0]['one_new_record_explains_two_old'])
        # Every legal pairing is one-to-one: no new tree appears twice.
        for pairing in disputes[0]['legal_pairings']:
            new_used = [edge.split('->')[1].split('[')[0] for edge in pairing['edges']]
            self.assertEqual(len(new_used), len(set(new_used)))
            old_used = [edge.split('->')[0] for edge in pairing['edges']]
            self.assertEqual(len(old_used), len(set(old_used)))
        # The dispute creates exactly one field task, not one per old tree.
        self.assertEqual(len(field['tasks']), 1)
        self.assertEqual(field['tasks'][0]['reason'], 'identity_dispute')
        # And neither old tree is treated as already remeasured by a candidate.
        self.assertEqual(len(field['tasks'][0]['old_trees']), 2)

    def test_candidate_only_link_requires_verification_task(self):
        # New tree carries a fresh id n; only the candidate connects it to old a.
        new = bundle([plot('p1')], [visit('v1', trees=[('n', 21), ('b', 31)])])
        candidates = overlay(tree_candidates=[tree_cid('c1', 'v1', 'a', 'v1', 'n')])
        field, stats = reconcile(self.old, new, candidates, [])
        reasons = [t['reason'] for t in field['tasks']]
        self.assertIn('candidate_verify', reasons)
        verify = next(t for t in field['tasks'] if t['reason'] == 'candidate_verify')
        self.assertEqual([t['tree'] for t in verify['old_trees']], ['a'])
        self.assertEqual([t['tree'] for t in verify['new_trees']], ['n'])
        self.assertEqual(stats['blocking_disputes'], [])

    def test_field_packet_has_no_weights_or_areas(self):
        candidates = overlay(tree_candidates=[tree_cid('c1', 'v1', 'a', 'v1', 'b')])
        field, _ = reconcile(self.old, self.new, candidates, [])

        def walk(obj, trail='root'):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    self.assertNotIn(key, FORBIDDEN_FIELD_KEYS, trail)
                    self.assertFalse(str(key).endswith('_ha'), trail)
                    walk(value, f'{trail}.{key}')
            elif isinstance(obj, list):
                for i, value in enumerate(obj):
                    walk(value, f'{trail}[{i}]')
        walk(field)

    def test_field_safety_guard_fails_closed_on_leak(self):
        from plotledger.reconcile import assert_field_safe
        with self.assertRaises(ValueError):
            assert_field_safe({'tasks': [{'represented_ha': 4}]})
        with self.assertRaises(ValueError):
            assert_field_safe({'weight': 1})

    def test_legal_pairings_never_change_queue_priority(self):
        candidates = overlay(tree_candidates=[
            tree_cid('c1', 'v1', 'a', 'v1', 'b'),
            tree_cid('c2', 'v1', 'b', 'v1', 'a'),
        ])
        _field, stats = reconcile(self.old, self.new, candidates, [])
        effect = stats['blocking_disputes'][0]['priority_effect']
        self.assertFalse(effect['priority_changes_between_legal_pairings'])

    def test_relocating_candidate_is_rejected(self):
        old = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1'), visit('v2', plot='p2')])
        new = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1'), visit('v2', plot='p2')])
        candidates = overlay(tree_candidates=[tree_cid('c1', 'v1', 'a', 'v2', 'x')])
        with self.assertRaises(ValueError):
            validate_candidates(candidates, Editions(old, new))

    def test_duplicate_candidate_pair_is_rejected(self):
        candidates = overlay(tree_candidates=[
            tree_cid('c1', 'v1', 'a', 'v1', 'b'),
            tree_cid('c2', 'v1', 'a', 'v1', 'b'),
        ])
        with self.assertRaises(ValueError):
            validate_candidates(candidates, self.editions)

    def test_unknown_references_are_rejected(self):
        candidates = overlay(tree_candidates=[tree_cid('c1', 'v1', 'ghost', 'v1', 'a')])
        with self.assertRaises(ValueError):
            validate_candidates(candidates, self.editions)

    def test_coverage_gaps_queue_unvisited_cells_by_area(self):
        # p1 pine 6 ha visited (dispute-free), p2 pine 8 ha inaccessible.
        old = bundle([plot('p1', expansion=6), plot('p2', expansion=8)],
                     [visit('v1', trees=[('a', 20)]),
                      visit('v2', plot='p2', status='inaccessible')])
        new = bundle([plot('p1', expansion=6), plot('p2', expansion=8)],
                     [visit('v1', trees=[('a', 21)]),
                      visit('v2', plot='p2', status='inaccessible')])
        field, stats = reconcile(old, new, overlay(), [])
        gaps = [t for t in field['tasks'] if t['reason'] == 'coverage_gap']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['plot'], 'p2')
        self.assertEqual(gaps[0]['task_id'], 'R-001')
        row = next(r for r in stats['coverage_cells'] if r['plot'] == 'p2')
        self.assertFalse(row['observed_for_density'])

    def test_priority_order_puts_larger_area_first(self):
        old = bundle([plot('p1', expansion=2), plot('p2', expansion=8)],
                     [visit('v1', trees=[('a', 20)]),
                      visit('v2', plot='p2', status='inaccessible')])
        new = bundle([plot('p1', expansion=2), plot('p2', expansion=8)],
                     [visit('v1', trees=[('a', 21)]),
                      visit('v2', plot='p2', status='inaccessible')])
        field, _stats = reconcile(old, new, overlay(), [])
        self.assertEqual([t['plot'] for t in field['tasks']], ['p2'])

    def test_packets_share_common_ids_for_cross_check(self):
        candidates = overlay(tree_candidates=[tree_cid('c1', 'v1', 'a', 'v1', 'b')])
        field, stats = reconcile(self.old, self.new, candidates, [])
        field_ids = {t['task_id'] for t in field['tasks']}
        self.assertEqual(field_ids, set(stats['cross_check']['field_task_ids']))
        for task in field['tasks']:
            self.assertTrue(all(k in task for k in ('plot', 'cover', 'visit', 'task_id')))

    def test_visited_empty_cell_is_not_a_coverage_gap(self):
        old = bundle([plot('p1')], [visit('v1', trees=[])])
        new = bundle([plot('p1')], [visit('v1', trees=[])])
        field, _ = reconcile(old, new, overlay(), [])
        self.assertEqual(field['tasks'], [])

    def test_editions_must_share_survey_and_diameter_threshold(self):
        other = copy.deepcopy(self.new)
        other['survey'] = 'other'
        with self.assertRaises(ValueError):
            Editions(self.old, other)
        other = copy.deepcopy(self.new)
        other['min_dbh_cm'] = 15
        with self.assertRaises(ValueError):
            Editions(self.old, other)

    def test_output_is_deterministic(self):
        candidates = overlay(tree_candidates=[
            tree_cid('c1', 'v1', 'a', 'v1', 'b'),
            tree_cid('c2', 'v1', 'b', 'v1', 'a'),
        ])
        first = encode(reconcile(self.old, self.new, candidates, []))
        second = encode(reconcile(self.old, self.new, candidates, []))
        self.assertEqual(first, second)


class ComponentMechanicsTests(unittest.TestCase):
    def test_cross_cell_components_are_rejected_at_validation(self):
        old = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1', trees=[('a', 20)]), visit('v2', plot='p2', trees=[('x', 20)])])
        new = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1', trees=[('a', 21)]), visit('v2', plot='p2', trees=[('x', 21)])])
        editions = Editions(old, new)
        with self.assertRaises(ValueError):
            validate_candidates(
                overlay(tree_candidates=[tree_cid('c1', 'v1', 'a', 'v2', 'x')]), editions)

    def test_component_guard_rejects_multicell_link_set(self):
        # Hand-built links bypassing validation exercise the defense-in-depth
        # cell guard inside build_components.
        old = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1', trees=[('a', 20)]), visit('v2', plot='p2', trees=[('x', 20)])])
        new = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v1', trees=[('a', 21)]), visit('v2', plot='p2', trees=[('x', 21)])])
        editions = Editions(old, new)
        links = [{'candidate_id': 'c1', 'old_visit': 'v1', 'old_tree': 'a',
                  'new_visit': 'v2', 'new_tree': 'x', 'cell': ('p1', 'pine')}]
        with self.assertRaises(ValueError):
            build_components(editions, links, {'v1': 'v1', 'v2': 'v2'})
        old = bundle([plot('p1')], [visit('v1', trees=[('a', 20), ('b', 30)])])
        new = bundle([plot('p1')], [visit('v1', trees=[('a', 21), ('b', 31)])])
        editions = Editions(old, new)
        candidates = overlay(tree_candidates=[
            tree_cid('c1', 'v1', 'a', 'v1', 'b'),
            tree_cid('c2', 'v1', 'b', 'v1', 'a'),
        ])
        _vl, links = validate_candidates(candidates, editions)
        visit_map = resolve_visit_identity(editions, _vl)
        components = build_components(editions, links, visit_map)
        disputes, _co, _pc = analyse_components(editions, components)
        d = disputes[0]
        default = d['legal_pairings'][d['default_pairing_index']]
        self.assertEqual(default['overturns_proven_edges'], [])
        self.assertEqual(sorted(default['edges']), ['a->a', 'b->b'])


    def test_unmatched_trees_are_surfaced_to_statistician_only(self):
        # New tree n has no old counterpart; old tree b disappears from the new
        # edition. Neither is a candidate, so no field task is raised, but the
        # statistician must see both as unmatched records.
        old = bundle([plot('p1')], [visit('v1', trees=[('a', 20), ('b', 30)])])
        new = bundle([plot('p1')], [visit('v1', trees=[('a', 21), ('n', 40)])])
        field, stats = reconcile(old, new, overlay(), [])
        self.assertEqual(field['tasks'], [])
        orphans = stats['identity']['unmatched_records']
        self.assertEqual(orphans['old_trees_without_new_record'],
                         [{'visit': 'v1', 'tree': 'b'}])
        self.assertEqual(orphans['new_trees_without_old_record'],
                         [{'visit': 'v1', 'tree': 'n'}])
        # No field task is raised for unmatched records without a candidate.
        self.assertEqual(field['tasks'], [])

    def test_renumbered_visit_candidate_carries_proven_tree_identity(self):
        old = bundle([plot('p1')], [visit('v9', trees=[('a', 20), ('b', 30)])])
        new = bundle([plot('p1')], [visit('v10', trees=[('a', 21), ('b', 31)])])
        candidates = overlay(visit_candidates=[
            {'id': 'vc1', 'old_visit': 'v9', 'new_visit': 'v10'}])
        field, stats = reconcile(old, new, candidates, [])
        # The accepted renumbering makes the trees proven; no revisit dispatched.
        self.assertEqual(field['tasks'], [])
        self.assertEqual(stats['identity']['deduped_without_redispatch']['proven_trees'], 2)
        link = stats['identity']['visit_candidates'][0]
        self.assertFalse(link['confirms_proven_identity'])

    def test_relocating_visit_candidate_is_rejected(self):
        old = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v9', trees=[('a', 20)]),
                      visit('v8', plot='p2', trees=[('z', 20)])])
        new = bundle([plot('p1'), plot('p2', expansion=2)],
                     [visit('v10', trees=[('a', 21)]),
                      visit('v11', plot='p2', trees=[('z', 21)])])
        candidates = overlay(visit_candidates=[
            {'id': 'vc1', 'old_visit': 'v9', 'new_visit': 'v11'}])
        with self.assertRaises(ValueError):
            reconcile(old, new, candidates, [])

    def test_duplicate_visit_per_cell_is_rejected(self):
        old = bundle([plot('p1')],
                     [visit('v9', trees=[('a', 20)]), visit('v8', trees=[('z', 20)])])
        new = bundle([plot('p1')], [visit('v10', trees=[('a', 21)])])
        with self.assertRaises(ValueError):
            Editions(old, new)

    def test_uncontested_visit_renumbering_is_accepted_then_verified(self):
        old = bundle([plot('p1')], [visit('v9', trees=[('a', 20)])])
        new = bundle([plot('p1')], [visit('v10', trees=[('a', 21), ('b', 31)])])
        candidates = overlay(visit_candidates=[
            {'id': 'vc1', 'old_visit': 'v9', 'new_visit': 'v10'}])
        field, stats = reconcile(old, new, candidates, [])
        # Tree a is proven through the accepted renumbering; new b has no old
        # record and creates no dispute, but it is surfaced for the statistician.
        proven = stats['identity']['proven_trees']
        self.assertEqual(proven, [{'visit': 'v9', 'tree': 'a'}])
        self.assertEqual([t for t in field['tasks']
                          if t['reason'] == 'visit_identity_dispute'], [])

    def test_visit_candidate_colliding_with_proven_visit_is_blocked(self):
        old = bundle([plot('p1')], [visit('v1', trees=[('a', 20)])])
        new = bundle([plot('p1')], [visit('v1', trees=[('a', 21)])])
        candidates = overlay(visit_candidates=[
            {'id': 'vc1', 'old_visit': 'v1', 'new_visit': 'v1'}])
        field, stats = reconcile(old, new, candidates, [])
        # Same-id visit is already proven; the candidate is labelled as such and
        # creates no task.
        self.assertEqual(field['tasks'], [])
        self.assertTrue(stats['identity']['visit_candidates'][0]['confirms_proven_identity'])


    def test_multi_cover_plot_treats_covers_as_separate_cells(self):
        multi_plot = {'id': 'p1', 'district': 'North', 'plot_m2': 400,
                      'expansion_ha': 4,
                      'segments': [
                          {'id': 'p1a', 'cover': 'pine', 'fraction': '3/4'},
                          {'id': 'p1b', 'cover': 'mixed', 'fraction': '1/4'}]}
        old = bundle([multi_plot], [
            visit('v1', cover='pine', trees=[('a', 20)]),
            visit('v2', cover='mixed', trees=[('m', 30)])])
        new = bundle([multi_plot], [
            visit('v1', cover='pine', trees=[('a', 21)]),
            visit('v2', cover='mixed', trees=[('m', 31)])])
        field, stats = reconcile(old, new, overlay(), [])
        self.assertEqual(field['tasks'], [])
        cells = {(r['plot'], r['cover']): r['represented_ha']
                 for r in stats['coverage_cells']}
        self.assertEqual(cells[('p1', 'pine')], 3)
        self.assertEqual(cells[('p1', 'mixed')], 1)

    def test_pairing_enumeration_is_capped_and_flagged(self):
        from plotledger import reconcile as mod
        old_trees = [('t%02d' % i, 20) for i in range(12)]
        new_trees = [('t%02d' % i, 21) for i in range(12)]
        old = bundle([plot('p1')], [visit('v1', trees=old_trees)])
        new = bundle([plot('p1')], [visit('v1', trees=new_trees)])
        editions = Editions(old, new)
        # Dense candidate graph: each old tree links to several new trees, so
        # the number of one-to-one pairings exceeds MAX_PAIRINGS.
        links = [{'candidate_id': f'c{i}-{j}', 'old_visit': 'v1',
                  'old_tree': f't{i:02d}', 'new_visit': 'v1',
                  'new_tree': f't{j:02d}', 'cell': ('p1', 'pine')}
                 for i in range(6) for j in range(i, min(i + 4, 12))]
        components = build_components(editions, links, {})
        disputes, _co, _pc = analyse_components(editions, components)
        self.assertTrue(disputes[0]['pairing_enumeration_truncated'])
        self.assertLessEqual(len(disputes[0]['legal_pairings']), mod.MAX_PAIRINGS)


if __name__ == '__main__':
    unittest.main()
