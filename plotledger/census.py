from collections import Counter

from .common import number, require, scalar, unique


def observations(plots, visits, min_dbh):
    """Validated per plot-cover observations, before any expansion weight is applied.

    Each row keeps the admitted diameters plus the plot geometry and cover
    share.  The full-design facts and every replicate design rebuild their
    numerators and denominators from these same terms, so only the expansion
    weight differs between designs.
    """
    rows = []
    seen = set()
    for key, row in sorted(unique(visits).items()):
        plot, cover = row['plot'], row['cover']
        district, area, weight, shares = plots[plot]
        share = shares.get(cover, 0)
        require(share > 0 and (plot, cover) not in seen, f'unknown or duplicate plot-cover visit: {key}')
        seen.add((plot, cover))
        state = row['status']
        require(state in ('visited', 'inaccessible', 'not_scheduled'), f'unknown status: {state}')
        trees = unique(row['trees'])
        require(state == 'visited' or not trees, f'unvisited plot has trees: {key}')
        admitted = {name: number(tree['dbh_cm'], 'diameter') for name, tree in trees.items()}
        admitted = {k: v for k, v in admitted.items() if v >= min_dbh}
        rows.append({'visit': key, 'plot': plot, 'district': district, 'cover': cover,
                     'status': state, 'area_m2': area, 'share': share, 'admitted': admitted})
    for key, (district, area, weight, shares) in plots.items():
        for cover, share in sorted(shares.items()):
            if share > 0 and (key, cover) not in seen:
                rows.append({'visit': None, 'plot': key, 'district': district, 'cover': cover,
                             'status': 'not_recorded', 'area_m2': area, 'share': share,
                             'admitted': {}})
    return rows


def expand(observations_, weights):
    """Expand observations with one weight per plot into publishable facts."""
    facts = []
    for row in observations_:
        plot = row['plot']
        # Basal area is represented as a rational coefficient of pi m^2.
        unit = weights[plot] / (row['area_m2'] / 10_000)
        visited = row['status'] == 'visited'
        classes = Counter()
        for diameter in row['admitted'].values():
            classes[str((diameter // 10) * 10)] += unit
        facts.append({'visit': row['visit'], 'plot': plot, 'district': row['district'],
                      'cover': row['cover'], 'status': row['status'],
                      'admitted_trees': sorted(row['admitted']),
                      'represented_ha': scalar(weights[plot] * row['share']),
                      'expanded_trees': scalar(unit * len(row['admitted'])) if visited else None,
                      'expanded_basal_pi_m2': scalar(unit * sum(d * d for d in row['admitted'].values()) / 40_000)
                      if visited else None,
                      'expanded_by_10cm_class': {k: scalar(v) for k, v in sorted(classes.items())}})
    return facts


def default_weights(plots):
    return {plot: row[2] for plot, row in plots.items()}
