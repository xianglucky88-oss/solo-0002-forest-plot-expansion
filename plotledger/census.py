from collections import Counter

from .common import number, require, scalar, unique


def visit_facts(plots, visits, min_dbh):
    facts = []
    seen = set()
    for key, row in sorted(unique(visits).items()):
        plot, cover = row['plot'], row['cover']
        record = plots[plot]
        district, area, weight, shares = (record['district'], record['area_m2'],
                                          record['weight_ha'], record['shares'])
        share = shares.get(cover, 0)
        require(share > 0 and (plot, cover) not in seen, f'unknown or duplicate plot-cover visit: {key}')
        seen.add((plot, cover))
        state = row['status']
        require(state in ('visited', 'inaccessible', 'not_scheduled'), f'unknown status: {state}')
        trees = unique(row['trees'])
        require(state == 'visited' or not trees, f'unvisited plot has trees: {key}')
        admitted = {name: number(tree['dbh_cm'], 'diameter') for name, tree in trees.items()}
        admitted = {k: v for k, v in admitted.items() if v >= min_dbh}
        # Basal area is represented as a rational coefficient of pi m^2.
        unit = weight / (area / 10_000)
        classes = Counter()
        for diameter in admitted.values():
            classes[str((diameter // 10) * 10)] += unit
        facts.append({'visit': key, 'plot': plot, 'district': district, 'cover': cover, 'status': state,
                      'admitted_trees': sorted(admitted), 'represented_ha': scalar(weight * share),
                      'expanded_trees': scalar(unit * len(admitted)) if state == 'visited' else None,
                      'expanded_basal_pi_m2': scalar(unit * sum(d * d for d in admitted.values()) / 40_000)
                      if state == 'visited' else None,
                      'expanded_by_10cm_class': {k: scalar(v) for k, v in sorted(classes.items())}})
    for key, record in plots.items():
        district, weight, shares = record['district'], record['weight_ha'], record['shares']
        for cover, share in sorted(shares.items()):
            if share > 0 and (key, cover) not in seen:
                facts.append({'visit': None, 'plot': key, 'district': district, 'cover': cover, 'status': 'not_recorded',
                              'admitted_trees': [], 'represented_ha': scalar(weight * share), 'expanded_trees': None,
                              'expanded_basal_pi_m2': None, 'expanded_by_10cm_class': {}})
    return facts
