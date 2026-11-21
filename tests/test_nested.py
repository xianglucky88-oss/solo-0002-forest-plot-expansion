#!/usr/bin/env python3
"""End-to-end checks for the concentric small/large circle reconciliation."""
import json
import sqlite3
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plotledger.workflow import materialize  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, '-m', 'plotledger', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True)


def bundle(readings, threshold=20, min_dbh=10, expansion=4):
    return {'survey': 'test', 'min_dbh_cm': min_dbh,
            'plots': [{'id': 'p1', 'district': 'N', 'plot_m2': 400, 'expansion_ha': expansion,
                       'segments': [{'id': 's', 'cover': 'pine', 'fraction': 1}]}],
            'visits': [{'id': 'v1', 'plot': 'p1', 'cover': 'pine', 'status': 'visited',
                        'nested': {'design': {'threshold_cm': threshold,
                                              'small': {'plot_m2': 100, 'radius_cm': 500},
                                              'large': {'plot_m2': 400, 'radius_cm': 1000}},
                                   'readings': readings}}]}


def reading(rid, tree, circle, dbh, low, high, remeasure='none'):
    return {'id': rid, 'tree': tree, 'circle': circle, 'dbh_cm': dbh,
            'distance_cm': [low, high], 'remeasure': remeasure}


def tree(result, name):
    return next(t for t in result['plot_cover_facts'][0]['trees'] if t['tree'] == name)


def check_merged_denominator():
    # Two raw records each admissible by its own initial reading: small-circle
    # r1 at 19 cm, large-circle r2 at 22 cm.  The accepted remeasure keeps r2
    # only, so the tree contributes one large-circle denominator (25 trees/ha
    # at 4 ha / 400 m2) and not both units.
    result = materialize(bundle([reading('r1', 't', 'small', 19, 0, 300),
                                 reading('r2', 't', 'large', 22, 200, 400, 'accepted')]))
    placed = tree(result, 't')
    assert placed['status'] == 'admitted', placed
    assert placed['design'] == 'large', placed
    assert placed['adopted_records'] == ['r2'], placed
    assert placed['records'] == ['r1', 'r2'], placed
    assert placed['expansion_unit'] == 100, placed
    visit = result['plot_cover_facts'][0]
    assert visit['expanded_trees'] == 100, visit
    assert visit['expanded_basal_pi_m2'] == '121/100', visit
    assert result['districts'][0]['trees_per_visited_ha'] == 25, result['districts'][0]


def check_both_circles_without_remeasure_count_once():
    # The same tree appears in both circles with equal initial diameters.  It
    # is one physical tree, so it gets one small-design denominator, not two.
    result = materialize(bundle([reading('r1', 't', 'small', 14, 0, 300),
                                 reading('r2', 't', 'large', 14, 0, 300)]))
    placed = tree(result, 't')
    assert (placed['design'], placed['status'], placed['expansion_unit']) == ('small', 'admitted', 400), placed
    assert placed['adopted_records'] == ['r1', 'r2'], placed
    assert result['plot_cover_facts'][0]['expanded_trees'] == 400, result['plot_cover_facts'][0]
    assert tree(result, 't')['expanded_basal_pi_m2'] == '49/25'


def check_cover_share_keeps_density_denominator():
    # A 1/2 cover share halves visited_ha but the expansion unit stays the
    # full unequal-probability weight, matching the legacy report's density
    # convention: 400 expanded trees over 2 represented cover hectares.
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)])
    doc['plots'][0]['segments'] = [{'id': 's1', 'cover': 'pine', 'fraction': '1/2'},
                                   {'id': 's2', 'cover': 'mixed', 'fraction': '1/2'}]
    doc['visits'][0]['cover'] = 'mixed'
    result = materialize(doc)
    report = result['districts'][0]
    assert (report['cover'], report['visited_ha'], report['trees_per_visited_ha']) == ('mixed', 2, 200), report
    assert report['basal_pi_m2_per_visited_ha'] == '18/25', report


def check_redesign_moves_small_to_large():
    # A small-circle initial reading at 19 cm would be a small design, but the
    # accepted remeasure at 26 cm moves the same tree to the large design.
    result = materialize(bundle([reading('r1', 't', 'small', 19, 0, 300),
                                 reading('r2', 't', 'large', 26, 100, 300, 'accepted')]))
    placed = tree(result, 't')
    assert (placed['design'], placed['status'], placed['expansion_unit']) == ('large', 'admitted', 100), placed


def check_redesign_can_exclude():
    # Final class below threshold puts the tree on the small design even though
    # it was recorded in the large circle; at 520 cm it is outside 500 cm, so
    # the large-circle reading cannot rescue it.
    result = materialize(bundle([reading('r1', 't', 'large', 12, 520, 600)]))
    placed = tree(result, 't')
    assert (placed['design'], placed['status'], placed['reason']) == ('small', 'excluded', 'outside_design'), placed


def check_boundary_is_unconfirmed():
    # Proof of existence (inside the small circle) but the range straddles the
    # selected boundary, so inclusion cannot be confirmed.
    result = materialize(bundle([reading('r1', 't', 'small', 14, 450, 620)]))
    placed = tree(result, 't')
    assert (placed['status'], placed['reason']) == ('unconfirmed', 'boundary'), placed
    assert placed['expanded_trees'] is None, placed
    assert result['plot_cover_facts'][0]['admitted_trees'] == [], result['plot_cover_facts'][0]
    assert result['districts'][0]['trees_per_visited_ha'] == 0, result['districts'][0]
    assert result['districts'][0]['unconfirmed_trees'] == [{'visit': 'v1', 'tree': 't', 'reason': 'boundary'}]


def check_rejected_remeasure_keeps_initial():
    result = materialize(bundle([reading('r1', 't', 'small', 12, 0, 300),
                                 reading('r2', 't', 'small', 13, 0, 300, 'rejected')]))
    placed = tree(result, 't')
    assert placed['adopted_records'] == ['r1'] and placed['dbh_cm'] == 12, placed
    assert placed['expansion_unit'] == 400, placed


def check_unresolved_only_tree_is_unconfirmed():
    result = materialize(bundle([reading('r1', 't', 'small', 12, 0, 300, 'unresolved')]))
    placed = tree(result, 't')
    assert (placed['status'], placed['reason']) == ('unconfirmed', 'unresolved_remeasure'), placed
    assert placed['expanded_trees'] is None, placed
    assert result['plot_cover_facts'][0]['expanded_trees'] == 0


def check_rejected_only_tree_is_unconfirmed():
    result = materialize(bundle([reading('r1', 't', 'small', 12, 0, 300, 'rejected')]))
    assert tree(result, 't')['reason'] == 'rejected_remeasure'


def expect_rejected(doc, fragment):
    try:
        materialize(doc)
    except ValueError as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f'accepted invalid bundle: {fragment}')


def check_rejections():
    expect_rejected(bundle([reading('r1', 't', 'small', 12, 0, 300),
                            reading('r2', 't', 'large', 14, 0, 300)]),
                    'conflicting adopted diameters')
    expect_rejected(bundle([reading('r1', 't', 'small', 12, 0, 300),
                            reading('r2', 't', 'large', 12, 800, 900)]),
                    'conflicting adopted distances')
    expect_rejected(bundle([reading('r1', 't', 'small', 12, 501, 600)]), 'outside its small circle')
    expect_rejected(bundle([reading('r1', 't', 'small', 12, 300, 200)]), 'minimum exceeds maximum')
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)])
    doc['visits'][0]['nested']['design']['large'] = {'plot_m2': 300, 'radius_cm': 900}
    expect_rejected(doc, 'areas must match their radii')
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)])
    doc['visits'].append({'id': 'v2', 'plot': 'p1', 'cover': 'pine', 'status': 'visited',
                          'trees': []})
    expect_rejected(doc, 'cannot mix nested and flat visits')
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)])
    doc['visits'][0]['status'] = 'inaccessible'
    expect_rejected(doc, 'unvisited plot has trees')
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)])
    doc['visits'][0]['trees'] = []
    expect_rejected(doc, 'mixes flat and nested readings')
    expect_rejected(bundle([reading('r1', 't', 'small', 20, 0, 300)]),
                    'small-circle reading at or above the threshold')


def check_legacy_untouched():
    for name in ('baseline', 'changed'):
        before = (ROOT / 'tests' / 'data' / f'{name}.preview.json').read_text()
        result = run('preview', ROOT / 'examples' / f'{name}.json')
        assert result.returncode == 0, result.stderr
        assert result.stdout == before, f'{name} legacy output changed'
    bad = run('preview', ROOT / 'examples' / 'invalid.json')
    assert bad.returncode == 2 and 'fractions must add to one' in bad.stderr, bad


def check_nested_example_is_frozen():
    expected = json.loads((ROOT / 'tests' / 'data' / 'nested.preview.json').read_text())
    result = run('preview', ROOT / 'examples' / 'nested.json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    facts = expected['plot_cover_facts'][0]
    assert facts['admitted_trees'] == ['t-overlap', 't-small'], facts
    assert facts['expanded_trees'] == 500, facts
    assert facts['expanded_by_10cm_class'] == {'10': 400, '20': 100}, facts
    assert facts['unconfirmed_trees'] == [{'tree': 't-boundary', 'reason': 'boundary'}], facts
    overlap = next(t for t in facts['trees'] if t['tree'] == 't-overlap')
    assert (overlap['design'], overlap['adopted_records'], overlap['records']) == ('large', ['r2'], ['r1', 'r2']), overlap
    assert expected['districts'][0]['trees_per_visited_ha'] == 125


def check_archive_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'ledger.sqlite'
        out = run('publish', ROOT / 'examples' / 'nested.json', archive, 'n1')
        assert out.returncode == 0, out.stderr
        shown = run('show', archive, 'n1')
        assert shown.returncode == 0, shown.stderr
        replay = run('replay', archive, 'n1')
        assert replay.returncode == 0, replay.stderr
        assert replay.stdout == shown.stdout
        again = run('publish', ROOT / 'examples' / 'nested.json', archive, 'n1')
        assert again.returncode == 2 and 'already exists' in again.stderr, again
        with sqlite3.connect(archive) as db:
            source, result = db.execute('SELECT source, result FROM editions').fetchone()
        assert json.loads(source) == json.loads((ROOT / 'examples' / 'nested.json').read_text())
        assert json.loads(result)['plot_cover_facts'][0]['expanded_trees'] == 500


def check_fractions_exact():
    doc = bundle([reading('r1', 't', 'small', 12, 0, 300)], expansion=3)
    result = materialize(doc)
    unit = Fraction(3) / Fraction(100, 10_000)
    assert unit == 300
    assert result['plot_cover_facts'][0]['expanded_trees'] == 300
    assert tree(result, 't')['expanded_basal_pi_m2'] == scalar(Fraction(300 * 144, 40_000))


def scalar(value):
    return value.numerator if value.denominator == 1 else str(value)


def main():
    checks = [value for name, value in sorted(globals().items()) if name.startswith('check_')]
    for check in checks:
        check()
        print(f'ok {check.__name__}')
    print(f'{len(checks)} checks passed')


if __name__ == '__main__':
    main()
