"""Build retrospective correction bundles for ridge-demo.

Atomic, single-team changes (no reducer change, no invented visits):

D  design team: positive cover-share migration on p2 plus a pair of
   weight corrections, chosen so North/mixed stays numerically
   invariant once the zero-tree visit v3 moves into that cover:
     * p2 pine share 1 -> 1/2 and a new mixed segment p2b at 1/2;
     * p2 expansion_ha 2 -> 4, so its new mixed half still represents
       the same 2 ha that v3 had on the pine side (unit 5 -> 10);
     * p3 expansion_ha 6 -> 4, cancelling p2's +2 ha so the North
       design total is unchanged (p3 is inaccessible and never enters
       a published density). The South plot p4 is left for patch S
       alone, keeping S independently attributable.
   Every share stays strictly positive and the fractions still sum
   to one; no visit is created or removed by the design side.
F  field team: migrate the *existing* zero-tree visit v3 (p2, pine,
   visited) to (p2, mixed). Nothing new is recorded and no tree is
   added; the visit keeps id v3 and its empty tree list.
S  independent design-team change on South plot p4: pine 1/2 -> 3/8,
   mixed 1/2 -> 5/8. Reachable standalone; touches no visit at all.

Bundles written to retro/bundles, each also previewed via the frozen
entry point so rejection text comes from the shipped validation path.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from plotledger.common import decode, encode  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = decode((ROOT / 'input' / 'baseline.json').read_text(encoding='utf-8'))


def find_plot(doc, plot_id):
    return next(p for p in doc['plots'] if p['id'] == plot_id)


def find_visit(doc, visit_id):
    return next(v for v in doc['visits'] if v['id'] == visit_id)


def apply_d(doc):
    find_plot(doc, 'p2')['expansion_ha'] = 4
    p2 = find_plot(doc, 'p2')
    pine = next(s for s in p2['segments'] if s['cover'] == 'pine')
    pine['fraction'] = '1/2'
    p2['segments'].append({'id': 'p2b', 'cover': 'mixed', 'fraction': '1/2'})
    find_plot(doc, 'p3')['expansion_ha'] = 4


def apply_f(doc):
    v3 = find_visit(doc, 'v3')
    v3['cover'] = 'mixed'


def apply_s(doc):
    p4 = find_plot(doc, 'p4')
    shares = {'pine': '3/8', 'mixed': '5/8'}
    for seg in p4['segments']:
        seg['fraction'] = shares[seg['cover']]


PATCHES = {'D': apply_d, 'F': apply_f, 'S': apply_s}
COMBOS = ['D', 'F', 'S', 'DF', 'DS', 'FS', 'DFS']


def build(name):
    doc = json.loads(json.dumps(BASE))  # deep copy of decoded structure
    for letter in name:
        PATCHES[letter](doc)
    path = ROOT / 'bundles' / f'{name}.json'
    path.write_text(encode(doc) + '\n', encoding='utf-8')
    return path


def preview(path):
    # The CLI is the project's own package; -I would imply -P and hide the
    # cwd from -m resolution, so the subprocess uses a plain -m invocation.
    proc = subprocess.run(
        [sys.executable, '-m', 'plotledger', 'preview', str(path)],
        cwd=REPO, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def main():
    rows = []
    for name in COMBOS:
        path = build(name)
        code, out, err = preview(path)
        rows.append({'bundle': name, 'path': str(path.relative_to(REPO)),
                     'exit': code, 'rejected': code != 0, 'error': err or None,
                     'result': json.loads(out) if code == 0 else None})
    print(encode(rows))


if __name__ == '__main__':
    main()
