"""Replayable release-impact driver for the ridge-demo retrospective.

Publishes every *admissible* bundle through the frozen CLI entry point
into one fresh immutable ledger, replays each published edition, and
probes rejected combos via preview (nothing rejected is ever written).
Outputs:
  retro/ledger/retro.sqlite           immutable editions
  retro/bundles/run_log.json          per-combo exit status and CLI text
  retro/bundles/hashes.json           source/result hashes of editions
The script refuses to touch an existing ledger so runs stay repeatable.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'ledger' / 'retro.sqlite'

# Baseline plus the seven subset combos; order fixes the edition sequence.
EDITIONS = [
    ('E0-baseline', 'input/baseline.json'),
    ('E1-D', 'bundles/D.json'),
    ('E2-S', 'bundles/S.json'),
    ('E3-DF', 'bundles/DF.json'),
    ('E4-DS', 'bundles/DS.json'),
    ('E5-DFS', 'bundles/DFS.json'),
]
REJECTED = [('F', 'bundles/F.json'), ('FS', 'bundles/FS.json')]


def cli(*args):
    proc = subprocess.run([sys.executable, '-m', 'plotledger', *args],
                          cwd=REPO, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def main():
    if LEDGER.exists():
        raise SystemExit(f'ledger already exists, move it aside first: {LEDGER}')
    log, hashes = [], []

    for edition, rel in EDITIONS:
        code, out, err = cli('publish', str(ROOT / rel), str(LEDGER), edition)
        assert code == 0, f'expected publish to accept {edition}: {err}'
        rec = json.loads(out)
        hashes.append({'edition': edition, **rec})
        rcode, rout, rerr = cli('replay', str(LEDGER), edition)
        assert rcode == 0, f'replay failed for {edition}: {rerr}'
        log.append({'edition': edition, 'bundle': rel, 'publish_exit': code,
                    'publish_output': rec, 'replay_exit': rcode})

    for name, rel in REJECTED:
        code, out, err = cli('preview', str(ROOT / rel))
        assert code == 2, f'expected rejection for {name}, got exit {code}'
        log.append({'edition': None, 'bundle': rel, 'combo': name,
                    'preview_exit': code, 'reject_reason': err})

    hcode, hout, herr = cli('history', str(LEDGER))
    assert hcode == 0, herr
    history = json.loads(hout)

    (ROOT / 'bundles' / 'run_log.json').write_text(
        json.dumps(log, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'bundles' / 'hashes.json').write_text(
        json.dumps({'editions': hashes, 'history': history},
                   ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ledger': str(LEDGER.relative_to(REPO)),
                      'published': len(EDITIONS), 'rejected': len(REJECTED),
                      'history': history}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
