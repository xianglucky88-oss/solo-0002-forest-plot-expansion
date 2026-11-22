"""Exact (Fraction-based) delta analysis over the retrospective editions.

Reads published results straight from the immutable ledger via the
project's own load() helper, so analysis never re-implements reduction.
Writes:
  retro/bundles/impact_matrix.json   full machine-readable diff set
  retro/bundles/invariant_check.json numeric-invariant / visit-reference proof
"""
import json
import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from plotledger.archive import load  # noqa: E402
from plotledger.common import encode  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'ledger' / 'retro.sqlite'
EDITIONS = ['E0-baseline', 'E1-D', 'E2-S', 'E3-DF', 'E4-DS', 'E5-DFS']
NUMERIC = ('design_ha', 'visited_ha', 'unobserved_ha',
           'trees_per_visited_ha', 'basal_pi_m2_per_visited_ha')


def q(value):
    return None if value is None else Fraction(str(value))


def reports(doc):
    return {(r['district'], r['cover']): r for r in doc['districts']}


def facts(doc):
    return {(f['plot'], f['cover']): f for f in doc['plot_cover_facts']}


def diff_report(a, b):
    out = {}
    for key in NUMERIC:
        x, y = q(a[key]), q(b[key])
        if x != y:
            out[key] = {'from': a[key], 'to': b[key],
                        'delta': str(y - x) if (x is not None and y is not None)
                        else None}
    if a['visits'] != b['visits']:
        out['visits'] = {'from': a['visits'], 'to': b['visits']}
    for key in ('class_trees_per_visited_ha',):
        if a[key] != b[key]:
            out[key] = {'from': a[key], 'to': b[key]}
    return out


def diff_fact(a, b):
    keys = ('status', 'represented_ha', 'expanded_trees',
            'expanded_basal_pi_m2', 'visit', 'expanded_by_10cm_class',
            'admitted_trees')
    return {k: {'from': a[k], 'to': b[k]} for k in keys if a[k] != b[k]}


def main():
    docs = {e: load(LEDGER, e) for e in EDITIONS}
    base = docs['E0-baseline']

    # 1. Every edition vs baseline, report level.
    vs_base = {}
    for e in EDITIONS[1:]:
        changes = {}
        rb, re_ = reports(base), reports(docs[e])
        for key in sorted(rb):
            d = diff_report(rb[key], re_[key])
            if d:
                changes['/'.join(key)] = d
        vs_base[e] = changes

    # 2. Marginal moves that isolate each patch's contribution.
    marginals = {}
    pairs = [('D alone: E0->E1', 'E0-baseline', 'E1-D'),
             ('S alone: E0->E2', 'E0-baseline', 'E2-S'),
             ('F after D: E1->E3', 'E1-D', 'E3-DF'),
             ('S after D: E1->E4', 'E1-D', 'E4-DS'),
             ('D after S: E2->E4', 'E2-S', 'E4-DS'),
             ('S after DF: E3->E5', 'E3-DF', 'E5-DFS'),
             ('F after DS: E4->E5', 'E4-DS', 'E5-DFS')]
    for label, ea, eb in pairs:
        changes = {}
        ra, rb = reports(docs[ea]), reports(docs[eb])
        for key in sorted(ra):
            d = diff_report(ra[key], rb[key])
            if d:
                changes['/'.join(key)] = d
        marginals[label] = changes

    # 3. Fact-level move of v3 (the migrated real visit) across editions.
    fact_path = {}
    v3_numeric_keys = ('represented_ha', 'expanded_trees',
                       'expanded_basal_pi_m2', 'expanded_by_10cm_class')
    for e in EDITIONS:
        for f in docs[e]['plot_cover_facts']:
            if f['visit'] == 'v3':
                fact_path[e] = {k: f[k] for k in
                                ('plot', 'cover', 'status', 'represented_ha',
                                 'expanded_trees', 'expanded_basal_pi_m2',
                                 'admitted_trees',
                                 'expanded_by_10cm_class')}
    v3_e0, v3_e3 = fact_path['E0-baseline'], fact_path['E3-DF']
    v3_invariant = {
        k: {'E0-baseline': v3_e0[k], 'E3-DF': v3_e3[k],
            'numerically_identical': v3_e0[k] == v3_e3[k]}
        for k in v3_numeric_keys}
    v3_invariant['reference_change'] = {
        'E0-baseline': {k: v3_e0[k] for k in ('plot', 'cover')},
        'E3-DF': {k: v3_e3[k] for k in ('plot', 'cover')}}

    # 4. Numeric-invariant / visit-reference proof for North/mixed.
    target = ('North', 'mixed')
    nm = {}
    for e in EDITIONS:
        r = reports(docs[e])[target]
        nm[e] = {k: r[k] for k in (*NUMERIC, 'visits',
                                   'class_trees_per_visited_ha')}
    invariant = {
        'report': '/'.join(target),
        'numeric_fields_identical_between_E0_and_E3': all(
            nm['E0-baseline'][k] == nm['E3-DF'][k] for k in NUMERIC),
        'class_densities_identical_between_E0_and_E3':
            nm['E0-baseline']['class_trees_per_visited_ha']
            == nm['E3-DF']['class_trees_per_visited_ha'],
        'visits_change': {'E0-baseline': nm['E0-baseline']['visits'],
                          'E3-DF': nm['E3-DF']['visits']},
        'rows': nm,
    }

    # 5. South null-density persistence (no zero back-fill anywhere).
    south = {}
    for e in EDITIONS:
        south[e] = {k: reports(docs[e])[('South', k)]
                    for k in ('pine', 'mixed')}
        for cover in ('pine', 'mixed'):
            row = south[e][cover]
            assert row['trees_per_visited_ha'] is None
            assert row['basal_pi_m2_per_visited_ha'] is None
            assert row['visits'] == []

    out = {'editions': EDITIONS, 'versus_baseline': vs_base,
           'marginals': marginals, 'v3_fact_path': fact_path,
           'v3_numeric_invariant': v3_invariant}
    (ROOT / 'bundles' / 'impact_matrix.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'bundles' / 'invariant_check.json').write_text(
        json.dumps(invariant, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    print(encode({'report_invariant':
                  invariant['numeric_fields_identical_between_E0_and_E3'],
                  'report_class_invariant':
                  invariant['class_densities_identical_between_E0_and_E3'],
                  'visits': invariant['visits_change'],
                  'v3_path': fact_path,
                  'v3_numeric_invariant': v3_invariant}))


if __name__ == '__main__':
    main()
