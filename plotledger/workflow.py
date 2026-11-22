from collections import defaultdict
from fractions import Fraction

from .census import visit_facts
from .common import number, require, scalar
from .design import load_plots
from .replacement import load_roster, normalized_roster, plot_locations, solve

DESCRIPTION = 'Publish weighted inventory based on supported plot-cover visits.'


def _adoption(direct_area, direct_basal, replacement_area, replacement_basal, extrema, line, best_count):
    supported = direct_area + replacement_area
    if not supported:
        return None
    density = (direct_basal + replacement_basal) / supported
    if line is None:
        return {'line_pi_m2_per_ha': None, 'status': 'no_adoption_line',
                'basal_pi_m2_per_ha': scalar(density)}
    # When no direct visit backs this cover group, the adoption question is
    # decided by the replacement evidence alone, not by unrelated plots.
    if direct_area == 0:
        judged_density = replacement_basal / replacement_area if replacement_area else None
        lo = extrema.get('min_replacement_density')
        hi = extrema.get('max_replacement_density')
    else:
        judged_density = density
        lo, hi = extrema.get('min_density'), extrema.get('max_density')
    if judged_density is None:
        return None
    if best_count <= 1 or lo is None or hi is None or (lo < line) == (hi < line):
        status = 'stable_above' if judged_density >= line else 'stable_below'
    else:
        status = 'pending_choice'
    return {
        'line_pi_m2_per_ha': scalar(line), 'status': status,
        'basal_pi_m2_per_ha': scalar(judged_density),
        'min_basal_pi_m2_per_ha': scalar(lo) if lo is not None else None,
        'max_basal_pi_m2_per_ha': scalar(hi) if hi is not None else None,
        'min_replacement_ha': scalar(extrema.get('min_replacement_ha', 0)),
        'max_replacement_ha': scalar(extrema.get('max_replacement_ha', 0)),
    }


def summarize(facts, replacement_lookup=None, extrema=None, best_count=0, adoption_line=None,
              has_roster=False):
    replacement_lookup = replacement_lookup or {}
    extrema = extrema or {}
    groups = defaultdict(list)
    for fact in facts:
        groups[fact['district'], fact['cover']].append(fact)
    reports = []
    for (district, cover), rows in sorted(groups.items()):
        used = [r for r in rows if r['status'] == 'visited']
        planned = sum(Fraction(r['represented_ha']) for r in rows)
        observed = sum(Fraction(r['represented_ha']) for r in used)
        trees = sum(Fraction(r['expanded_trees']) for r in used)
        basal = sum(Fraction(r['expanded_basal_pi_m2']) for r in used)
        classes = defaultdict(int)
        for row in used:
            for key, value in row['expanded_by_10cm_class'].items():
                classes[key] += Fraction(value)
        report = {'district': district, 'cover': cover, 'design_ha': scalar(planned),
                  'visited_ha': scalar(observed), 'unobserved_ha': scalar(planned - observed),
                  'trees_per_visited_ha': scalar(trees / observed) if observed else None,
                  'basal_pi_m2_per_visited_ha': scalar(basal / observed) if observed else None,
                  'class_trees_per_visited_ha': {k: scalar(v / observed) for k, v in sorted(classes.items())},
                  'visits': [r['visit'] for r in used]}
        if has_roster:
            contributions = [c for row in rows
                             for c in replacement_lookup.get((row['plot'], cover), [])]
            replacement_area = sum((c['represented_ha'] for c in contributions), Fraction(0))
            replacement_trees = sum((c['expanded_trees'] for c in contributions), Fraction(0))
            replacement_basal = sum((c['expanded_basal_pi_m2'] for c in contributions), Fraction(0))
            for contribution in contributions:
                for key, value in contribution['expanded_by_10cm_class'].items():
                    classes[key] += value
            supported = observed + replacement_area
            report['replacement_ha'] = scalar(replacement_area)
            report['supported_ha'] = scalar(supported)
            report['replacement_trees'] = scalar(replacement_trees)
            report['replacement_basal_pi_m2'] = scalar(replacement_basal)
            report['trees_per_supported_ha'] = scalar((trees + replacement_trees) / supported) if supported else None
            report['basal_pi_m2_per_supported_ha'] = (
                scalar((basal + replacement_basal) / supported) if supported else None)
            report['class_trees_per_supported_ha'] = (
                {k: scalar(v / supported) for k, v in sorted(classes.items())} if supported else {})
            report['replacement_visits'] = sorted({c['visit'] for c in contributions})
            adoption = _adoption(observed, basal, replacement_area, replacement_basal,
                                 extrema.get((district, cover), {}), adoption_line, best_count)
            if adoption is not None:
                report['adoption'] = adoption
        reports.append(report)
    return reports


def materialize(doc):
    plots = load_plots(doc['plots'])
    minimum = number(doc['min_dbh_cm'], 'minimum diameter')
    facts = visit_facts(plots, doc['visits'], minimum)
    roster_rows = doc.get('replacement_roster')
    if 'replacement_roster' not in doc:
        return {'survey': doc['survey'], 'min_dbh_cm': scalar(minimum),
                'plot_cover_facts': facts, 'districts': summarize(facts)}
    require(isinstance(roster_rows, list) and roster_rows,
            'replacement roster must be a non-empty list')

    locations = plot_locations(doc)
    districts = {district for district, *_ in plots.values()}
    candidates = load_roster(roster_rows, districts, minimum)
    solution, canonical_lookup, extrema, best_count = solve(facts, candidates, locations)
    adoption_line = None
    if doc.get('adoption_basal_pi_m2_per_ha') is not None:
        adoption_line = number(doc['adoption_basal_pi_m2_per_ha'], 'adoption line')
    return {'survey': doc['survey'], 'min_dbh_cm': scalar(minimum),
            'plot_cover_facts': facts,
            'replacement_roster': normalized_roster(candidates),
            'replacement_allocation': solution,
            'districts': summarize(facts, canonical_lookup, extrema, best_count, adoption_line,
                                   has_roster=True)}
