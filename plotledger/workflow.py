from collections import defaultdict
from fractions import Fraction

from . import replication
from .census import default_weights, expand, observations
from .common import number, scalar
from .design import load_plots

DESCRIPTION = 'Publish weighted inventory based on supported plot-cover visits.'


def summarize(facts):
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
        reports.append({'district': district, 'cover': cover, 'design_ha': scalar(planned),
                        'visited_ha': scalar(observed), 'unobserved_ha': scalar(planned - observed),
                        'trees_per_visited_ha': scalar(trees / observed) if observed else None,
                        'basal_pi_m2_per_visited_ha': scalar(basal / observed) if observed else None,
                        'class_trees_per_visited_ha': {k: scalar(v / observed) for k, v in sorted(classes.items())},
                        'visits': [r['visit'] for r in used]})
    return reports


def materialize(doc):
    plots = load_plots(doc['plots'])
    minimum = number(doc['min_dbh_cm'], 'minimum diameter')
    observed = observations(plots, doc['visits'], minimum)
    facts = expand(observed, default_weights(plots))
    result = {'survey': doc['survey'], 'min_dbh_cm': scalar(minimum),
              'plot_cover_facts': facts, 'districts': summarize(facts)}
    rows = replication.declared(doc)
    if rows is not None:
        result['replications'] = replication.analyze(rows, plots, observations=observed)
    return result
