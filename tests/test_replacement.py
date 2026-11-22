"""Acceptance checks for replacement rosters and legacy behaviour.

Run with: python3 -I tests/test_replacement.py
No third-party dependencies; the tests stay outside the package directory.
"""
import sqlite3
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plotledger.archive import load, publish  # noqa: E402
from plotledger.common import decode, encode  # noqa: E402
from plotledger.workflow import materialize  # noqa: E402

BUNDLE = ROOT / 'examples' / 'replacement.json'
BASELINE = ROOT / 'examples' / 'baseline.json'
CHANGED = ROOT / 'examples' / 'changed.json'
INVALID = ROOT / 'examples' / 'invalid.json'


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f'  ok - {message}')


def copy_doc(doc):
    return decode(encode(doc))


def main():
    print('legacy inputs stay byte-identical without a roster')
    for path in (BASELINE, CHANGED):
        before = subprocess.run([sys.executable, '-m', 'plotledger', 'preview', str(path)],
                                cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        doc = decode(Path(path).read_text(encoding='utf-8'))
        check(encode(materialize(doc)) == before, f'{path.name} preview unchanged')

    print('invalid legacy input is still rejected with status 2')
    proc = subprocess.run([sys.executable, '-m', 'plotledger', 'preview', str(INVALID)],
                          cwd=ROOT, capture_output=True, text=True)
    check(proc.returncode == 2, 'invalid.json exits 2')

    doc = decode(BUNDLE.read_text(encoding='utf-8'))
    result = materialize(doc)
    allocation = result['replacement_allocation']
    canonical = allocation['canonical']
    greedy = allocation['nearest_greedy']

    print('optimal allocation beats nearest-point greedy')
    check(Fraction(canonical['total_ha']) == 13, 'canonical covers 13 ha')
    check(Fraction(greedy['total_ha']) == 9, 'nearest greedy only covers 9 ha')
    check(Fraction(allocation['lost_ha_vs_demand']) == 7, '7 ha of demand stay unsupported')
    check(canonical['optimal_allocation_count'] == 2, 'two equal-area optimal allocations')

    print('candidate assignment constraints')
    assigned = [a['candidate'] for a in canonical['assignments']]
    check(len(assigned) == len(set(assigned)), 'one candidate assigned at most once')
    by_target = {}
    for a in canonical['assignments']:
        by_target.setdefault((a['plot'], a['cover']), []).append(a)
    demands = {(t['plot'], t['cover']): Fraction(t['demand_ha'])
               for t in allocation['eligible_targets']}
    for target, rows in by_target.items():
        covered = sum(Fraction(r['represented_ha']) for r in rows)
        check(covered <= demands[target], f'{target} stays within original represented area')

    print('non-public and unusable candidates are reported, not silently used')
    reasons = {u['candidate']: u['reason'] for u in allocation['unassigned_candidates']}
    check(reasons.get('c8') == 'not_public', 'private candidate c8 excluded')
    check(reasons.get('c7') == 'no_feasible_target', 'tied c7 noted as unassigned')

    print('roster provenance and support notes')
    roster = {c['id']: c for c in result['replacement_roster']}
    check(roster['c8']['public_direct'] is False, 'roster keeps public_direct flag')
    cover_notes = {v['cover']: v['represented_ha'] for c in result['replacement_roster']
                   for v in c['visits']}
    check(cover_notes['pine'] is not None, 'candidate cover support is listed')

    print('district adoption verdicts')
    verdicts = {(d['district'], d['cover']): d['adoption'] for d in result['districts']
                if 'adoption' in d}
    check(verdicts[('Ridge', 'mixed')]['status'] == 'pending_choice',
          'equal-area allocations crossing the line are pending_choice')
    check(Fraction(verdicts[('Ridge', 'mixed')]['min_basal_pi_m2_per_ha']) == 0
          and Fraction(verdicts[('Ridge', 'mixed')]['max_basal_pi_m2_per_ha']) == Fraction('1/4'),
          'pending extrema straddle the 1/8 line')
    check(verdicts[('Ridge', 'pine')]['status'] == 'stable_below',
          'zero-tree replacement is evidence of zero, not a missing match')
    check(verdicts[('South', 'pine')]['status'] == 'stable_below',
          'zero basal area stays below the adoption line')
    check(('South', 'mixed') not in verdicts, 'group with no evidence gets no verdict')
    check(verdicts[('North', 'pine')]['status'] == 'stable_above', 'direct evidence group stable')
    check(verdicts[('North', 'pine')]['line_pi_m2_per_ha'] == '1/8', 'line echoed rationally')

    print('direct visit results are untouched')
    direct = {f['visit']: f for f in result['plot_cover_facts'] if f['status'] == 'visited'}
    check(Fraction(direct['v1']['expanded_basal_pi_m2']) == Fraction('5/4'),
          'v1 expanded basal unchanged')
    check(Fraction(direct['v3']['expanded_trees']) == 0, 'visited zero stays a real zero')

    print('rejection rules')
    def reject(mutate, label):
        bad = copy_doc(doc)
        mutate(bad)
        try:
            materialize(bad)
        except ValueError:
            print(f'  ok - rejected: {label}')
            return
        raise AssertionError(f'should reject: {label}')

    reject(lambda d: d['replacement_roster'][0].__setitem__('public_direct', 'yes'),
           'non-boolean public_direct')
    reject(lambda d: d['replacement_roster'][0].__setitem__('expansion_ha', 0),
           'nonpositive candidate weight')
    def duplicate_visit(d):
        d['replacement_roster'][1]['visits'][0]['id'] = d['replacement_roster'][0]['visits'][0]['id']
    reject(duplicate_visit, 'duplicate candidate visit id')
    reject(lambda d: d['replacement_roster'][0].__setitem__('id', 'c2'),
           'duplicate candidate id')
    reject(lambda d: d['replacement_roster'].append({
        'id': 'cx', 'district': 'Nowhere', 'location': [0, 0], 'plot_m2': 400,
        'expansion_ha': 1, 'public_direct': True,
        'visits': [{'id': 'cxv', 'cover': 'pine', 'status': 'visited', 'trees': []}]}),
        'unknown design domain')
    reject(lambda d: d['replacement_roster'][0].__setitem__('location', [1]),
           'malformed location')
    reject(lambda d: d['replacement_roster'][0].pop('location'),
           'missing candidate location')
    reject(lambda d: d['plots'][0].pop('location'),
           'missing plot location needed for nearest fallback')
    reject(lambda d: d.__setitem__('replacement_roster', []),
           'empty roster')
    reject(lambda d: d['replacement_roster'][0]['visits'][0].__setitem__('fraction', '1/2'),
           'candidate fractions do not add to one')
    reject(lambda d: d['replacement_roster'][0]['visits'][0].__setitem__(
        'status', 'inaccessible') or d['replacement_roster'][0]['visits'][0].__setitem__(
        'trees', [{'id': 'x', 'dbh_cm': 20}]),
        'trees on unvisited candidate')
    reject(lambda d: d['replacement_roster'][0].pop('visits'),
           'candidate without visits list')
    reject(lambda d: d.__setitem__('adoption_basal_pi_m2_per_ha', 'not-a-number'),
           'non-rational adoption line')

    print('cross-domain candidate cannot serve another domain')
    shifted = copy_doc(doc)
    for c in shifted['replacement_roster']:
        if c['id'] == 'c1':
            c['district'] = 'South'
    shifted_result = materialize(shifted)
    c1_targets = {a['plot'] for a in shifted_result['replacement_allocation']['canonical']['assignments']
                  if a['candidate'] == 'c1'}
    shifted_plots = {p['id']: p['district'] for p in shifted['plots']}
    check(c1_targets and all(shifted_plots[plot] == 'South' for plot in c1_targets),
          'c1 can only serve same-domain targets after moving to South')

    print('candidate larger than the target area is rejected for that target')
    oversized = copy_doc(doc)
    for c in oversized['replacement_roster']:
        if c['id'] == 'c9':
            c['expansion_ha'] = 2
    oversized_result = materialize(oversized)
    used = {a['candidate'] for a in oversized_result['replacement_allocation']['canonical']['assignments']}
    check('c9' not in used, '2 ha candidate cannot replace a 1 ha target')

    print('sqlite publish, replay and history')
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'ledger.sqlite'
        receipt = publish(archive, 'roster-v1', doc, result)
        check(len(receipt['source_sha256']) == 64 and len(receipt['result_sha256']) == 64,
              'sha-256 receipts present')
        stored = load(archive, 'roster-v1')
        check(encode(stored) == encode(result), 'published result round-trips')
        check(encode(load(archive, 'roster-v1', source=True)) == encode(doc),
              'sealed source round-trips')
        replayed = materialize(load(archive, 'roster-v1', source=True))
        check(encode(replayed) == encode(stored), 'replay matches stored result')
        try:
            publish(archive, 'roster-v1', doc, result)
        except ValueError:
            print('  ok - duplicate edition name rejected')
        else:
            raise AssertionError('duplicate edition should fail')
        with sqlite3.connect(archive) as db:
            names = [r[0] for r in db.execute('SELECT name FROM editions ORDER BY seq')]
        check(names == ['roster-v1'], 'one immutable edition stored')

    print('\nall acceptance checks passed')


if __name__ == '__main__':
    main()
