#!/usr/bin/env python3 -I
"""Independent checks for the overlapping-area rollup.

Runs the public CLI against the published example bundles and against bundles
generated in a separate scratch directory. Exits non-zero on the first failed
expectation. No third-party packages are used.
"""
import json
import os
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = str(ROOT)


def preview(bundle):
    env = dict(os.environ)
    env['PYTHONPATH'] = MODULE_ROOT
    # -E ignores site-level environment overrides while PYTHONPATH still
    # locates the project package; the verifier itself is launched with -I.
    proc = subprocess.run(
        [sys.executable, '-E', '-m', 'plotledger', 'preview', str(bundle)],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    return proc


def q(value):
    return Fraction(value) if value is not None else None


def subarea(doc, name):
    return next(row for row in doc['management']['subareas'] if row['name'] == name)


def region(doc, name):
    return next(row for row in doc['management']['regions'] if row['name'] == name)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f'  ok: {message}')


def proven_case(bundle, region_area, shared_label):
    proc = preview(bundle)
    check(proc.returncode == 0, f'{shared_label}: preview accepted')
    doc = json.loads(proc.stdout)
    a, b = subarea(doc, 'sub-a'), subarea(doc, 'sub-b')
    r = region(doc, 'macro-r')

    check(q(a['design_ha']) == q(b['design_ha']),
          f'{shared_label}: subareas have equal represented area ({a["design_ha"]})')
    check(q(a['trees_per_visited_ha']) == q(b['trees_per_visited_ha']) == 50,
          f'{shared_label}: subareas have equal tree density (50)')
    check(q(a['basal_pi_m2_per_visited_ha']) == q(b['basal_pi_m2_per_visited_ha']) == Fraction(1, 2),
          f'{shared_label}: subareas have equal basal density (1/2 pi m2/ha)')
    check(r['mode'] == 'exact', f'{shared_label}: region is exactly recomputable')
    check(q(r['design_ha']) == region_area,
          f'{shared_label}: overlap counted once -> region area {region_area}')
    check(q(r['trees_per_visited_ha']) == 50,
          f'{shared_label}: region density recomputed from original shares (50), not averaged')
    # Naive double counting would report the sum of the two subarea areas.
    check(q(r['design_ha']) < q(a['design_ha']) + q(b['design_ha']),
          f'{shared_label}: shared piece is not double counted')


def main():
    print('== equal subarea area + density, different true shared fraction ==')
    proven_case(ROOT / 'examples' / 'rollup_half.json', Fraction(20), 'half-shared (fraction 1/2)')
    proven_case(ROOT / 'examples' / 'rollup_full.json', Fraction(20), 'fully-shared (fraction 1)')

    proc = preview(ROOT / 'examples' / 'rollup_half.json')
    half = json.loads(proc.stdout)
    proc = preview(ROOT / 'examples' / 'rollup_full.json')
    full = json.loads(proc.stdout)
    ha = q(subarea(half, 'sub-a')['design_ha'])
    fa = q(subarea(full, 'sub-a')['design_ha'])
    check(ha != fa,
          f'cases differ in true overlap: half subarea {ha} ha vs full subarea {fa} ha')
    check(region(half, 'macro-r')['design_ha'] == region(full, 'macro-r')['design_ha'] == 20,
          'both cases recover the same 20 ha union of original land')

    print('== unproven overlap: only the supported range, no density, no averaging ==')
    proc = preview(ROOT / 'examples' / 'rollup_unproven.json')
    check(proc.returncode == 0, 'unproven bundle is accepted')
    doc = json.loads(proc.stdout)
    r = region(doc, 'macro-r')
    check(r['mode'] == 'unproven', 'region marked unproven')
    check(r['trees_per_visited_ha'] is None and r['basal_pi_m2_per_visited_ha'] is None,
          'densities withheld when the duplicate piece cannot be identified')
    check(r['class_trees_per_visited_ha'] == {}, 'no class densities published either')
    rng = r['supported_range_ha']
    check(q(rng['design_ha_min']) == 10 and q(rng['design_ha_max']) == 20,
          'union area bounded by max member (10) and sum of members (20)')
    check(q(rng['visited_ha_min']) == 10 and q(rng['visited_ha_max']) == 20,
          'visited area keeps its own range')
    check(r['unproven_edges'] == ['sub-a/sub-b'], 'unproven edge is named in the report')
    a, b = subarea(doc, 'sub-a'), subarea(doc, 'sub-b')
    check(q(a['design_ha']) == q(b['design_ha']) == 10 and
          q(a['trees_per_visited_ha']) == q(b['trees_per_visited_ha']) == 50,
          'subarea facts remain exactly supported underneath an unproven region')

    print('== visited zero stays separate from unvisited area ==')
    proc = preview(ROOT / 'examples' / 'rollup_mixed.json')
    check(proc.returncode == 0, 'mixed-status bundle accepted')
    doc = json.loads(proc.stdout)
    a = subarea(doc, 'sub-a')
    check(q(a['design_ha']) == 25 and q(a['visited_ha']) == 20 and q(a['unobserved_ha']) == 5,
          'sub-a: zero-tree visited plot in visited_ha, inaccessible plot in unobserved_ha')
    r = region(doc, 'macro-r')
    check(q(r['design_ha']) == 30 and q(r['visited_ha']) == 20 and q(r['unobserved_ha']) == 10,
          'region keeps both area bases distinct after overlap removal')
    check(q(r['trees_per_visited_ha']) == 25 and
          q(r['basal_pi_m2_per_visited_ha']) == Fraction(1, 4),
          'density divides only by visited area, zero visits included as zero')

    print('== invalid bundles must be rejected with status 2 ==')
    rejected = ROOT / 'examples' / 'rollup_invalid.json'
    proc = preview(rejected)
    check(proc.returncode == 2 and 'management boundary' in proc.stderr,
          'co-claimed fact without a declared edge is rejected')

    base = json.loads((ROOT / 'examples' / 'rollup_half.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)

        def reject( mutate, label):
            doc = json.loads(json.dumps(base))
            mutate(doc)
            path = scratch / (label.replace(' ', '_') + '.json')
            path.write_text(json.dumps(doc), encoding='utf-8')
            proc = preview(path)
            check(proc.returncode == 2, f'{label} -> exit 2 ({proc.stderr.strip()})')

        def wrong_shared(doc):
            doc['management']['overlap_edges'][0]['shared'][0]['fraction'] = '1/3'
        reject(wrong_shared, 'shared fraction different from a+b-1')

        def unproven_touch(doc):
            doc['management']['overlap_edges'][0]['kind'] = 'unproven'
            doc['management']['overlap_edges'][0].pop('shared')
        reject(unproven_touch, 'unproven edge touching a co-claimed fact')

        def empty_subarea(doc):
            doc['management']['subareas'].append({'id': 'sub-c'})
            doc['management']['regions'][0]['subareas'].append('sub-c')
        reject(empty_subarea, 'subarea without membership evidence')

        def too_big_share(doc):
            doc['management']['memberships'][0]['fraction'] = 2
        reject(too_big_share, 'direct share above one')

    print('\nAll rollup checks passed.')


if __name__ == '__main__':
    main()
