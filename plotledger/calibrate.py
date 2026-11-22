"""Calibrated estimation packages.

A calibration bundle adds, on top of the plain inventory fields:

* per-plot ``zone_shares``: cross-shares of the plot over the new map
  zones (they must add to one, like the cover segments);
* per-plot ``factor_bounds``: the survey party's acceptable interval
  [lo, hi] for the expansion factor g that multiplies the base
  expansion weight (lo must be positive, hi finite, so no plot can be
  inflated without limit to make the controls look good);
* ``controls``: control areas for the new map (``zone_cover``) and the
  comparable old-management margins (``district_cover``);
* ``density_thresholds``: business lines that published densities are
  read against.

Only visited plot-cover pairs support a control cell. A positive
control without any visited support is reported as ``no_visit_support``;
a supported system whose controls cannot be met inside the factor
bounds is ``incompatible_controls``. When both margin sets balance on
their own but not together, ``separately_feasible_jointly_infeasible``
is set. Published densities are exact intervals over the whole feasible
weight polytope, so uncertainty crossing a business line cannot be
hidden behind a single weight vector.
"""
from fractions import Fraction

from .common import number, require, scalar, unique
from .design import load_plots
from .exactlp import InfeasibleError, minimize

STATISTICS = ('trees_per_ha', 'basal_pi_m2_per_ha')
MARGINS = ('zone', 'district')


def _controls(raw):
    require(isinstance(raw, dict), 'controls must be an object')
    tables = {}
    for margin in MARGINS:
        table = raw.get(f'{margin}_cover', {})
        require(isinstance(table, dict), f'controls.{margin}_cover must be an object')
        cells = {}
        for name, covers in table.items():
            require(isinstance(name, str) and name.strip(), f'blank name in {margin}_cover')
            require(isinstance(covers, dict), f'controls.{margin}_cover.{name} must be an object')
            for cover, value in covers.items():
                require(isinstance(cover, str) and cover.strip(), f'blank cover in {margin}_cover')
                require((name, cover) not in cells, f'duplicate control cell: {name}/{cover}')
                cells[(name, cover)] = number(value, 'control area')
        tables[margin] = cells
    require(any(tables[margin] for margin in MARGINS), 'controls must list at least one cell')
    return tables


def _thresholds(raw, tables):
    require(isinstance(raw, list), 'density_thresholds must be a list')
    index = {}
    for row in raw:
        require(isinstance(row, dict), 'density threshold must be an object')
        margin = row.get('margin')
        require(margin in MARGINS, f'unknown threshold margin: {margin}')
        name = row.get(margin)
        require(isinstance(name, str) and name.strip(), f'threshold is missing the {margin} name')
        cover = row.get('cover')
        require(isinstance(cover, str) and cover.strip(), 'threshold is missing the cover')
        statistic = row.get('statistic')
        require(statistic in STATISTICS, f'unknown threshold statistic: {statistic}')
        threshold = number(row.get('threshold'), 'density threshold')
        require(threshold > 0, 'density threshold must be positive')
        require((name, cover) in tables[margin], f'threshold without a matching control cell: {name}/{cover}')
        key = (margin, name, cover, statistic)
        require(key not in index, f'duplicate density threshold: {margin}/{name}/{cover}/{statistic}')
        index[key] = threshold
    return index


def _visit_index(plots, visits, minimum):
    index = {}
    for key, row in sorted(unique(visits).items()):
        plot, cover = row['plot'], row['cover']
        require(plot in plots, f'unknown plot: {plot}')
        require(plots[plot]['shares'].get(cover, 0) > 0 and (plot, cover) not in index,
                f'unknown or duplicate plot-cover visit: {key}')
        state = row['status']
        require(state in ('visited', 'inaccessible', 'not_scheduled'), f'unknown status: {state}')
        trees = unique(row['trees'])
        require(state == 'visited' or not trees, f'unvisited plot has trees: {key}')
        admitted = [number(tree['dbh_cm'], 'diameter') for tree in trees.values()]
        index[(plot, cover)] = {'visit': key, 'status': state,
                                'diameters': [d for d in admitted if d >= minimum]}
    return index


def _build_cells(margin, table, plots, visits):
    cells = []
    for (name, cover), control in sorted(table.items()):
        design = Fraction(0)
        visited = Fraction(0)
        support = []
        for plot, record in plots.items():
            share = record['shares'].get(cover, Fraction(0))
            if share <= 0:
                continue
            if margin == 'zone':
                zone_share = record['zone_shares'].get(name, Fraction(0))
                if zone_share <= 0:
                    continue
                coefficient = record['weight_ha'] * zone_share * share
            else:
                if record['district'] != name:
                    continue
                zone_share = None
                coefficient = record['weight_ha'] * share
            design += coefficient
            visit = visits.get((plot, cover))
            if visit is not None and visit['status'] == 'visited':
                visited += coefficient
                support.append({'plot': plot, 'visit': visit['visit'], 'coefficient': coefficient,
                                'cover_share': share, 'zone_share': zone_share})
        cells.append({'margin': margin, 'name': name, 'cover': cover, 'control': control,
                      'design': design, 'visited': visited, 'support': support})
    return cells


def _require_controls_for_support(plots, visits, tables):
    missing = set()
    for plot, record in plots.items():
        for cover, share in record['shares'].items():
            if share <= 0:
                continue
            visit = visits.get((plot, cover))
            if visit is None or visit['status'] != 'visited':
                continue
            names = [zone for zone, value in record['zone_shares'].items() if value > 0]
            for margin, wanted in (('zone', names), ('district', [record['district']])):
                for name in wanted:
                    if (name, cover) not in tables[margin]:
                        missing.add(f'{margin}/{name}/{cover}')
    require(not missing, f'missing controls for supported cells: {sorted(missing)}')


def _system_rows(cells, var_index, lows, caps, with_bounds=True):
    rows = []
    for cell in cells:
        if not cell['support']:
            continue
        coeffs = [Fraction(0)] * len(var_index)
        shift = Fraction(0)
        for contributor in cell['support']:
            j = var_index[contributor['plot']]
            coeffs[j] += contributor['coefficient']
            shift += contributor['coefficient'] * lows[j]
        rows.append((coeffs, '=', cell['control'] - shift))
    if with_bounds:
        for i in range(len(var_index)):
            row = [Fraction(0)] * len(var_index)
            row[i] = Fraction(1)
            rows.append((row, '<=', caps[i]))
    return rows


def _feasible(rows, width):
    try:
        minimize([Fraction(0)] * width, rows, width)
        return True
    except InfeasibleError:
        return False


def _cell_ref(cell):
    ref = {'margin': cell['margin'], cell['margin']: cell['name'], 'cover': cell['cover']}
    ref['control_ha'] = scalar(cell['control'])
    return ref


def _assess(cells, var_index, lows, caps):
    unsupported = [c for c in cells if c['control'] > 0 and not c['support']]
    rows = _system_rows(cells, var_index, lows, caps)
    if unsupported:
        return {'status': 'infeasible', 'reason': 'no_visit_support',
                'unsupported_cells': [_cell_ref(c) for c in unsupported],
                'supported_part_feasible': _feasible(rows, len(var_index))}
    if _feasible(rows, len(var_index)):
        return {'status': 'feasible'}
    relaxed = _system_rows(cells, var_index, lows, caps, with_bounds=False)
    return {'status': 'infeasible', 'reason': 'incompatible_controls',
            'feasible_without_upper_bounds': _feasible(relaxed, len(var_index))}


def _certificate(control_rows, lows, caps):
    """One feasible weight vector, closest to the base factors in L1.

    Minimizing sum |g_i - 1| keeps the certificate as near the base
    expansion weights as the controls allow; the simplex tie-breaking
    makes the choice deterministic.
    """
    n = len(lows)
    rows = [(list(coeffs) + [Fraction(0)] * n, sense, b) for coeffs, sense, b in control_rows]
    for i in range(n):
        lower_dev = [Fraction(0)] * (2 * n)
        lower_dev[i] = Fraction(-1)
        lower_dev[n + i] = Fraction(1)
        rows.append((lower_dev, '>=', lows[i] - 1))
        upper_dev = [Fraction(0)] * (2 * n)
        upper_dev[i] = Fraction(1)
        upper_dev[n + i] = Fraction(1)
        rows.append((upper_dev, '>=', 1 - lows[i]))
        bound = [Fraction(0)] * (2 * n)
        bound[i] = Fraction(1)
        rows.append((bound, '<=', caps[i]))
    objective = [Fraction(0)] * n + [Fraction(1)] * n
    _, solution = minimize(objective, rows, 2 * n)
    return [solution[i] + lows[i] for i in range(n)]


def _density_coeffs(cell, plots, visits, var_index, statistic):
    num = [Fraction(0)] * len(var_index)
    den = [Fraction(0)] * len(var_index)
    for contributor in cell['support']:
        j = var_index[contributor['plot']]
        record = plots[contributor['plot']]
        unit = record['weight_ha'] / (record['area_m2'] / 10_000)
        diameters = visits[(contributor['plot'], cell['cover'])]['diameters']
        if statistic == 'trees_per_ha':
            stat = Fraction(len(diameters))
        else:
            stat = sum((d * d for d in diameters), Fraction(0)) / 40_000
        zone_share = contributor['zone_share'] if cell['margin'] == 'zone' else Fraction(1)
        num[j] += zone_share * unit * stat
        den[j] += contributor['coefficient']
    return num, den


def _density_interval(control_rows, lows, caps, num, den):
    """Exact [min, max] of num(g)/den(g) over the feasible polytope.

    Charnes-Cooper: with t = 1/den(g) and y = t·(g - L), the ratio
    becomes linear and the box bounds become y_i <= cap_i·t. The
    denominator is strictly positive on the feasible set because every
    supporting plot has a positive lower factor bound.
    """
    n = len(lows)
    num_const = sum((c * l for c, l in zip(num, lows)), Fraction(0))
    den_const = sum((c * l for c, l in zip(den, lows)), Fraction(0))
    rows = [(list(coeffs) + [-b], sense, Fraction(0)) for coeffs, sense, b in control_rows]
    for i in range(n):
        row = [Fraction(0)] * (n + 1)
        row[i] = Fraction(1)
        row[n] = -caps[i]
        rows.append((row, '<=', Fraction(0)))
    rows.append((list(den) + [den_const], '=', Fraction(1)))
    objective = list(num) + [num_const]
    lo, _ = minimize(objective, rows, n + 1)
    hi, _ = minimize([-c for c in objective], rows, n + 1)
    return lo, -hi


def _relation(lo, hi, threshold):
    if lo > threshold:
        return 'above'
    if hi < threshold:
        return 'below'
    if lo == hi == threshold:
        return 'on_line'
    if lo < threshold < hi:
        return 'straddles'
    return 'touches'


def _cell_output(cell, plots):
    out = _cell_ref(cell)
    out.update({'design_ha': scalar(cell['design']),
                'visited_base_ha': scalar(cell['visited']),
                'supported': bool(cell['support'])})
    low_rep = Fraction(0)
    high_rep = Fraction(0)
    support = []
    for contributor in cell['support']:
        lo, hi = plots[contributor['plot']]['factor_bounds']
        low_rep += contributor['coefficient'] * lo
        high_rep += contributor['coefficient'] * hi
        entry = {'plot': contributor['plot'], 'visit': contributor['visit'],
                 'coefficient_ha': scalar(contributor['coefficient']),
                 'cover_share': scalar(contributor['cover_share']),
                 'factor_bounds': [scalar(lo), scalar(hi)]}
        if cell['margin'] == 'zone':
            entry['zone_share'] = scalar(contributor['zone_share'])
        support.append(entry)
    out['min_representable_ha'] = scalar(low_rep)
    out['max_representable_ha'] = scalar(high_rep)
    out['support'] = support
    return out


def calibrate_package(doc):
    plots = load_plots(doc['plots'], calibration=True)
    minimum = number(doc['min_dbh_cm'], 'minimum diameter')
    visits = _visit_index(plots, doc['visits'], minimum)
    tables = _controls(doc['controls'])
    thresholds = _thresholds(doc.get('density_thresholds', []), tables)
    _require_controls_for_support(plots, visits, tables)

    cells = {margin: _build_cells(margin, tables[margin], plots, visits) for margin in MARGINS}
    active = sorted({s['plot'] for margin in MARGINS for c in cells[margin] for s in c['support']})
    var_index = {plot: i for i, plot in enumerate(active)}
    lows = [plots[p]['factor_bounds'][0] for p in active]
    caps = [plots[p]['factor_bounds'][1] - plots[p]['factor_bounds'][0] for p in active]

    feasibility = {f'{margin}_margins': _assess(cells[margin], var_index, lows, caps)
                   for margin in MARGINS}
    joint_cells = cells['zone'] + cells['district']
    feasibility['joint'] = _assess(joint_cells, var_index, lows, caps)
    joint_feasible = feasibility['joint']['status'] == 'feasible'
    pattern = (feasibility['zone_margins']['status'] == 'feasible'
               and feasibility['district_margins']['status'] == 'feasible'
               and not joint_feasible)

    weights = None
    densities = []
    if joint_feasible:
        control_rows = _system_rows(joint_cells, var_index, lows, caps, with_bounds=False)
        factors = _certificate(control_rows, lows, caps)
        weights = {plot: scalar(factors[var_index[plot]]) for plot in active}
        for cell in joint_cells:
            if not cell['support']:
                continue
            for statistic in STATISTICS:
                num, den = _density_coeffs(cell, plots, visits, var_index, statistic)
                lo, hi = _density_interval(control_rows, lows, caps, num, den)
                at_certificate = (sum((num[j] * factors[j] for j in range(len(active))), Fraction(0))
                                  / sum((den[j] * factors[j] for j in range(len(active))), Fraction(0)))
                entry = _cell_ref(cell)
                entry.update({'statistic': statistic,
                              'interval': [scalar(lo), scalar(hi)],
                              'at_certificate': scalar(at_certificate),
                              'threshold': None, 'relation': None, 'determinate': None})
                threshold = thresholds.get((cell['margin'], cell['name'], cell['cover'], statistic))
                if threshold is not None:
                    relation = _relation(lo, hi, threshold)
                    entry.update({'threshold': scalar(threshold), 'relation': relation,
                                  'determinate': relation in ('above', 'below', 'on_line')})
                densities.append(entry)

    return {'survey': doc['survey'], 'product': 'calibration-package',
            'min_dbh_cm': scalar(minimum),
            'outcome': 'feasible' if joint_feasible else 'infeasible',
            'separately_feasible_jointly_infeasible': pattern,
            'cells': [_cell_output(c, plots) for margin in MARGINS for c in cells[margin]],
            'feasibility': feasibility,
            'weights': weights,
            'inactive_plots': [p for p in sorted(plots) if p not in var_index],
            'densities': densities}
