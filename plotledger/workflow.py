from collections import defaultdict
from fractions import Fraction

from .census import visit_facts
from .common import number, require, scalar
from .design import load_plots
from .nested_census import nested_visit_facts

DESCRIPTION = 'Publish weighted inventory based on supported plot-cover visits.'


def summarize(facts, nested=False):
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
                  'class_trees_per_visited_ha': {k: scalar(v / observed) for k, v in sorted(classes.items())}}
        if nested:
            report['unconfirmed_trees'] = [{'visit': r['visit'], **t} for r in used
                                           for t in r['unconfirmed_trees']]
        report['visits'] = [r['visit'] for r in used]
        reports.append(report)
    return reports


def materialize(doc):
    plots = load_plots(doc['plots'])
    minimum = number(doc['min_dbh_cm'], 'minimum diameter')
    visits = doc['visits']
    require(all(isinstance(row, dict) for row in visits), 'visits must be objects')
    nested = any('nested' in row for row in visits)
    require(not nested or all('nested' in row for row in visits),
            'a bundle cannot mix nested and flat visits')
    facts = nested_visit_facts(plots, visits, minimum) if nested else visit_facts(plots, visits, minimum)
    return {'survey': doc['survey'], 'min_dbh_cm': scalar(minimum), 'plot_cover_facts': facts,
            'districts': summarize(facts, nested=nested)}
