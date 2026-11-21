"""Read a complete synthetic evidence bundle without external services."""
import argparse
import sqlite3
import sys
from fractions import Fraction
from pathlib import Path

from .archive import digest, history, load, publish
from .census import visit_facts
from .common import decode, encode, require
from .design import load_plots
from .reconcile import reconcile
from .workflow import DESCRIPTION, materialize, summarize


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('preview', help='compute results without writing an archive').add_argument('bundle', type=Path)
    rel = subs.add_parser('publish', help='commit one immutable edition')
    rel.add_argument('bundle', type=Path)
    rel.add_argument('archive', type=Path)
    rel.add_argument('edition')
    for command in ('show', 'source', 'replay'):
        p = subs.add_parser(command)
        p.add_argument('archive', type=Path)
        p.add_argument('edition')
    subs.add_parser('history').add_argument('archive', type=Path)
    rev = subs.add_parser('revisit', help='plan the next revisit queue from two sealed editions')
    rev.add_argument('archive', type=Path)
    rev.add_argument('old_edition')
    rev.add_argument('new_edition')
    rev.add_argument('candidates', type=Path)
    rev.add_argument('--field', type=Path, required=True, help='field-team packet output path')
    rev.add_argument('--stats', type=Path, required=True, help='statistician packet output path')
    args = parser.parse_args(argv)
    try:
        match args.command:
            case 'preview' | 'publish':
                doc = decode(args.bundle.read_text(encoding='utf-8'))
                require(isinstance(doc, dict), 'bundle must be an object')
                result = materialize(doc)
                if args.command == 'publish':
                    result = publish(args.archive, args.edition, doc, result)
            case 'history':
                result = history(args.archive)
            case 'replay':
                stored = load(args.archive, args.edition)
                result = materialize(load(args.archive, args.edition, source=True))
                require(encode(stored) == encode(result), f'replay differs: {args.edition}')
            case 'revisit':
                result = build_revisit(args)
            case _:
                result = load(args.archive, args.edition, source=args.command == 'source')
        print(encode(result))
        return 0
    except (OSError, ValueError, LookupError, TypeError, ArithmeticError, sqlite3.Error) as exc:
        print(f'{parser.prog}: {exc}', file=sys.stderr)
        return 2


def build_revisit(args):
    for path in (args.field, args.stats):
        require(not path.exists(), f'output already exists, refusing to overwrite: {path}')
    old_doc = load(args.archive, args.old_edition, source=True)
    new_doc = load(args.archive, args.new_edition, source=True)
    candidate_doc = decode(args.candidates.read_text(encoding='utf-8'))
    # Recompute coverage facts from the sealed new edition and cross-check them
    # against the published result before any queue is produced.
    minimum = Fraction(new_doc['min_dbh_cm'])
    facts = visit_facts(load_plots(new_doc['plots']), new_doc['visits'], minimum)
    district_rows = summarize(facts)
    published = load(args.archive, args.new_edition)
    require(encode(published['districts']) == encode(district_rows),
            f'published result is inconsistent with sealed source: {args.new_edition}')
    field_packet, stats_packet = reconcile(old_doc, new_doc, candidate_doc, district_rows)
    field_text, stats_text = encode(field_packet), encode(stats_packet)
    args.field.write_text(field_text + '\n', encoding='utf-8')
    args.stats.write_text(stats_text + '\n', encoding='utf-8')
    return {
        'survey': field_packet['survey'],
        'field_packet': str(args.field), 'stats_packet': str(args.stats),
        'field_sha256': digest(field_text), 'stats_sha256': digest(stats_text),
        'task_count': len(field_packet['tasks']),
        'task_ids': [t['task_id'] for t in field_packet['tasks']],
        'old_edition': args.old_edition, 'new_edition': args.new_edition,
    }
