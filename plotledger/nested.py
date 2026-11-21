"""Concentric small/large circle readings reconciled per physical tree.

A nested visit measures small trees in the small circle and large trees in the
large circle.  Overlap readings and remeasurements are tied together by the
``tree`` identity, so one physical tree is expanded once, under the single
circle design that its final diameter class selects.
"""
from collections import defaultdict

from .common import number, require, scalar, unique

CIRCLES = ('small', 'large')
REMEASURE_STATES = ('none', 'accepted', 'rejected', 'unresolved')


def load_nested(raw):
    """Validate the small/large circle geometry of one visit."""
    require(isinstance(raw, dict), 'nested design must be an object')
    threshold = number(raw.get('threshold_cm'), 'nested threshold')
    require(threshold > 0, 'nested threshold must be positive')
    circles = {}
    for name in CIRCLES:
        spec = raw.get(name)
        require(isinstance(spec, dict), f'nested {name} circle must be an object')
        area = number(spec.get('plot_m2'), f'nested {name} plot area')
        radius = number(spec.get('radius_cm'), f'nested {name} radius')
        require(area > 0 and radius > 0, f'nested {name} circle must be positive')
        circles[name] = {'plot_m2': area, 'radius_cm': radius}
    require(circles['small']['radius_cm'] < circles['large']['radius_cm'],
            'nested small radius must be less than large radius')
    require(circles['small']['plot_m2'] < circles['large']['plot_m2'],
            'nested small area must be less than large area')
    require(circles['small']['plot_m2'] * circles['large']['radius_cm'] ** 2
            == circles['large']['plot_m2'] * circles['small']['radius_cm'] ** 2,
            'nested circle areas must match their radii')
    return {'threshold_cm': threshold, 'circles': circles}


def _distance(raw, label):
    require(isinstance(raw, list) and len(raw) == 2, f'{label} must be [minimum, maximum]')
    low = number(raw[0], label)
    high = number(raw[1], label)
    require(low <= high, f'{label} minimum exceeds maximum')
    return low, high


def load_readings(raw):
    """Validate raw records.  Record ids are unique; tree ids may repeat."""
    require(isinstance(raw, list), 'nested readings must be a list')
    readings = []
    for record_id, row in sorted(unique(raw).items()):
        tree = row.get('tree')
        require(isinstance(tree, str) and tree.strip(),
                f'invalid tree identity for reading {record_id}')
        circle = row.get('circle')
        require(circle in CIRCLES, f'invalid circle for reading {record_id}: {circle!r}')
        diameter = number(row.get('dbh_cm'), f'{record_id} diameter')
        distance = _distance(row.get('distance_cm'), f'{record_id} distance')
        remeasure = row.get('remeasure', 'none')
        require(remeasure in REMEASURE_STATES,
                f'invalid remeasure status for reading {record_id}: {remeasure!r}')
        readings.append({'id': record_id, 'tree': tree, 'circle': circle, 'dbh_cm': diameter,
                         'distance_cm': distance, 'remeasure': remeasure})
    return readings


def _merge(records, tree):
    """One adopted measurement set: equal diameters, intersecting distance."""
    diameters = {record['dbh_cm'] for record in records}
    require(len(diameters) == 1, f'conflicting adopted diameters for tree {tree}')
    low = max(record['distance_cm'][0] for record in records)
    high = min(record['distance_cm'][1] for record in records)
    require(low <= high, f'conflicting adopted distances for tree {tree}')
    return records[0]['dbh_cm'], (low, high)


def _reading(record):
    return {'id': record['id'], 'circle': record['circle'], 'dbh_cm': scalar(record['dbh_cm']),
            'distance_cm': [scalar(record['distance_cm'][0]), scalar(record['distance_cm'][1])],
            'remeasure': record['remeasure']}


def resolve_tree(tree, records, design, min_dbh):
    """Resolve all readings of one physical tree to at most one legal design.

    * An accepted remeasurement is authoritative and replaces initial readings.
    * Otherwise initial readings are authoritative; rejected and unresolved
      remeasurements never become observations of their own.
    * A tree with records but no adopted measurement is known to exist and
      remains unconfirmed for inclusion.
    * The final diameter class selects small or large design.  A tree proven
      inside the small circle is automatically inside the large circle, so a
      final diameter at or above the threshold legitimately moves it to the
      large design; the reverse move can fall outside the small design.
    * A distance range that straddles the selected circle boundary cannot
      decide inclusion, so it is unconfirmed rather than a definite
      observation.
    """
    for record in records:
        radius = design['circles'][record['circle']]['radius_cm']
        require(record['distance_cm'][0] <= radius,
                f"{record['id']} lies outside its {record['circle']} circle")
        require(record['circle'] != 'small' or record['dbh_cm'] < design['threshold_cm'],
                f"{record['id']} is a small-circle reading at or above the threshold")
    accepted = [record for record in records if record['remeasure'] == 'accepted']
    initial = [record for record in records if record['remeasure'] == 'none']
    unresolved = [record for record in records if record['remeasure'] == 'unresolved']
    detail = [_reading(record) for record in records]
    record_ids = [record['id'] for record in records]
    placement = {'tree': tree, 'records': record_ids, 'readings': detail,
                 'adopted_records': [], 'basis': None, 'design': None, 'dbh_cm': None,
                 'class_10cm': None, 'distance_cm': None, 'plot_m2': None,
                 'radius_cm': None, 'expansion_unit': None, 'expanded_trees': None,
                 'expanded_basal_pi_m2': None, 'status': None, 'reason': None}

    if accepted:
        source, basis = accepted, 'accepted_remeasure'
    elif initial:
        source, basis = initial, 'initial'
    else:
        placement['status'] = 'unconfirmed'
        placement['reason'] = 'unresolved_remeasure' if unresolved else 'rejected_remeasure'
        return placement

    diameter, distance = _merge(source, tree)
    name = 'large' if diameter >= design['threshold_cm'] else 'small'
    circle = design['circles'][name]
    placement.update({'design': name, 'dbh_cm': scalar(diameter), 'distance_cm': [scalar(distance[0]), scalar(distance[1])],
                      'plot_m2': scalar(circle['plot_m2']), 'radius_cm': scalar(circle['radius_cm']),
                      'class_10cm': str((diameter // 10) * 10), 'adopted_records': [r['id'] for r in source],
                      'basis': basis})

    if diameter < min_dbh:
        placement.update({'status': 'excluded', 'reason': 'below_minimum'})
    elif distance[1] <= circle['radius_cm']:
        placement.update({'status': 'admitted', 'reason': None})
    elif distance[0] > circle['radius_cm']:
        placement.update({'status': 'excluded', 'reason': 'outside_design'})
    else:
        placement.update({'status': 'unconfirmed', 'reason': 'boundary'})
    return placement


def resolve_visit(readings, design, min_dbh):
    """Resolve every physical tree in a nested visit exactly once."""
    groups = defaultdict(list)
    for reading in readings:
        groups[reading['tree']].append(reading)
    return [resolve_tree(tree, groups[tree], design, min_dbh) for tree in sorted(groups)]
