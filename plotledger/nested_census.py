"""Per-tree nested visit facts and the current report's density reduction.

Legacy flat visits keep the original ``visit_facts`` path untouched so already
published editions replay to the same bytes.  Nested visits expose one
placement per physical tree plus the circle denominators that were actually
used, so a tree read in both circles is expanded once under its final design
and an undecidable boundary tree stays out of the definite observation count.
"""
from collections import Counter
from fractions import Fraction

from .common import require, scalar, unique
from .nested import load_nested, load_readings, resolve_visit


def _unit(weight, plot_m2):
    return weight / (Fraction(plot_m2) / 10_000)


def _design_fact(design, weight):
    circles = design['circles']
    return {'threshold_cm': scalar(design['threshold_cm']),
            'small_plot_m2': scalar(circles['small']['plot_m2']),
            'large_plot_m2': scalar(circles['large']['plot_m2']),
            'small_radius_cm': scalar(circles['small']['radius_cm']),
            'large_radius_cm': scalar(circles['large']['radius_cm']),
            'small_expansion_unit': scalar(_unit(weight, circles['small']['plot_m2'])),
            'large_expansion_unit': scalar(_unit(weight, circles['large']['plot_m2']))}


def _place_tree(placement, design, weight):
    """Attach the single legal expansion denominator for this tree, if any."""
    if placement['design'] is None:
        return placement
    circle = design['circles'][placement['design']]
    unit = _unit(weight, circle['plot_m2'])
    if placement['status'] == 'admitted':
        diameter = Fraction(placement['dbh_cm'])
        placement['expansion_unit'] = scalar(unit)
        placement['expanded_trees'] = scalar(unit)
        placement['expanded_basal_pi_m2'] = scalar(unit * diameter * diameter / 40_000)
    elif placement['status'] == 'excluded':
        placement['expansion_unit'] = None
        placement['expanded_trees'] = 0
        placement['expanded_basal_pi_m2'] = 0
    return placement


def nested_visit_facts(plots, visits, min_dbh):
    facts, seen = [], set()
    for key, row in sorted(unique(visits).items()):
        plot, cover = row['plot'], row['cover']
        district, area, weight, shares = plots[plot]
        share = shares.get(cover, 0)
        require(share > 0, f'unknown plot-cover visit: {key}')
        require((plot, cover) not in seen, f'duplicate plot-cover visit: {key}')
        seen.add((plot, cover))
        state = row['status']
        require(state in ('visited', 'inaccessible', 'not_scheduled'), f'unknown status: {state}')
        require('nested' in row, f'nested visit {key} lacks a nested design')
        require('trees' not in row, f'visit {key} mixes flat and nested readings')
        raw = row['nested']
        require(isinstance(raw, dict), f'nested visit {key} must be an object')
        design = load_nested(raw.get('design'))
        readings = load_readings(raw.get('readings', []))
        require(state == 'visited' or not readings, f'unvisited plot has trees: {key}')
        placements = resolve_visit(readings, design, min_dbh) if state == 'visited' else []
        placements = [_place_tree(p, design, weight) for p in placements]
        admitted = [p for p in placements if p['status'] == 'admitted']
        classes = Counter()
        for placement in admitted:
            classes[placement['class_10cm']] += Fraction(placement['expansion_unit'])
        represented = weight * share
        facts.append({'visit': key, 'plot': plot, 'district': district, 'cover': cover, 'status': state,
                      'admitted_trees': [p['tree'] for p in admitted],
                      'represented_ha': scalar(represented),
                      'expanded_trees': scalar(sum(Fraction(p['expanded_trees']) for p in admitted))
                      if state == 'visited' else None,
                      'expanded_basal_pi_m2': scalar(sum(Fraction(p['expanded_basal_pi_m2']) for p in admitted))
                      if state == 'visited' else None,
                      'expanded_by_10cm_class': {k: scalar(v) for k, v in sorted(classes.items())},
                      'design': _design_fact(design, weight), 'trees': placements,
                      'unconfirmed_trees': [{'tree': p['tree'], 'reason': p['reason']}
                                            for p in placements if p['status'] == 'unconfirmed'],
                      'excluded_trees': [{'tree': p['tree'], 'reason': p['reason']}
                                         for p in placements if p['status'] == 'excluded']})
    for key, (district, _, weight, shares) in plots.items():
        for cover, share in sorted(shares.items()):
            if share > 0 and (key, cover) not in seen:
                facts.append({'visit': None, 'plot': key, 'district': district, 'cover': cover,
                              'status': 'not_recorded', 'admitted_trees': [],
                              'represented_ha': scalar(weight * share), 'expanded_trees': None,
                              'expanded_basal_pi_m2': None, 'expanded_by_10cm_class': {},
                              'design': None, 'trees': [], 'unconfirmed_trees': [], 'excluded_trees': []})
    return facts
