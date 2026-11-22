"""Roll plot-cover facts up to overlapping management areas.

The same plots can feed more than one reporting area, and subareas are not
guaranteed to be mutually exclusive. A plot-cover fact is therefore assigned to
a subarea through an explicit direct share. Where two subareas genuinely
overlap, the shared piece must be counted exactly once against the original
plot-cover fraction; where the evidence cannot prove which overlap causes the
double count, only the still-supported range of areas can be reported.

Densities are recomputed from the original represented area and expanded
counts. Subarea densities are never averaged.
"""
from collections import defaultdict
from fractions import Fraction

from .common import number, require, scalar

UNPROVEN = 'unproven'


def _edge_key(a, b):
    return tuple(sorted((a, b)))


def _named_rows(rows, label, id_field):
    require(isinstance(rows, list), f'{label} must be a list')
    index = {}
    for row in rows:
        require(isinstance(row, dict), f'{label} entry must be an object')
        key = row.get(id_field)
        require(isinstance(key, str) and key.strip() and key not in index,
                f'invalid or duplicate {id_field}: {key!r}')
        index[key] = row
    return index


def _parse_management(management):
    require(isinstance(management, dict), 'management must be an object')
    subareas = _named_rows(management.get('subareas', []), 'subareas', 'id')
    regions = _named_rows(management.get('regions', []), 'regions', 'id')
    sub_ids = set(subareas)

    membership_rows = management.get('memberships', [])
    require(isinstance(membership_rows, list), 'memberships must be a list')
    memberships = defaultdict(list)
    for row in membership_rows:
        require(isinstance(row, dict), 'membership entry must be an object')
        sub = row.get('subarea')
        require(isinstance(sub, str) and sub in sub_ids, f'membership unknown subarea: {sub!r}')
        memberships[sub].append(row)

    edge_rows = management.get('overlap_edges', [])
    require(isinstance(edge_rows, list), 'overlap_edges must be a list')
    edges = {}
    for row in edge_rows:
        require(isinstance(row, dict), 'overlap edge must be an object')
        a, b = row.get('a'), row.get('b')
        require(a in sub_ids and b in sub_ids and a != b, f'invalid overlap edge: {row!r}')
        key = _edge_key(a, b)
        require(key not in edges, f'duplicate overlap edge: {a}/{b}')
        kind = row.get('kind')
        require(kind in ('proven', UNPROVEN), f'unknown overlap kind: {kind!r}')
        shared = {}
        if kind == 'proven':
            entries = row.get('shared', [])
            require(isinstance(entries, list), f'proven edge shared must be a list: {a}/{b}')
            for piece in entries:
                require(isinstance(piece, dict), f'shared piece must be an object on edge {a}/{b}')
                plot, cover = piece.get('plot'), piece.get('cover')
                require(isinstance(plot, str) and isinstance(cover, str),
                        f'invalid shared piece on edge {a}/{b}')
                token = (plot, cover)
                require(token not in shared, f'duplicate shared piece: {plot}/{cover}')
                shared[token] = number(piece.get('fraction'), 'shared fraction')
        edges[key] = {'kind': kind, 'shared': shared}
    return subareas, regions, memberships, edges


def _fact_index(facts):
    index = {}
    for fact in facts:
        index[(fact['plot'], fact['cover'])] = fact
    return index


def _load_assignments(sub, rows, fact_index):
    assignments = {}
    for row in rows:
        plot, cover = row.get('plot'), row.get('cover')
        require(isinstance(plot, str) and isinstance(cover, str),
                f'membership of {sub} lacks plot/cover')
        token = (plot, cover)
        require(token in fact_index, f'membership has no plot-cover fact: {plot}/{cover}')
        require(token not in assignments, f'duplicate membership in {sub}: {plot}/{cover}')
        share = number(row.get('fraction'), 'direct share')
        require(0 < share <= 1, f'direct share outside (0,1] in {sub}: {plot}/{cover}')
        assignments[token] = share
    return assignments


def _aggregate(contributions):
    """Sum prorated area and expanded counts; keep visit states distinct."""
    totals = {'design_ha': Fraction(0), 'visited_ha': Fraction(0), 'unobserved_ha': Fraction(0),
              'expanded_trees': Fraction(0), 'expanded_basal_pi_m2': Fraction(0),
              'classes': defaultdict(Fraction), 'visits': []}
    for fact, weight in contributions:
        area = Fraction(fact['represented_ha']) * weight
        totals['design_ha'] += area
        if fact['status'] == 'visited':
            totals['visited_ha'] += area
            totals['expanded_trees'] += Fraction(fact['expanded_trees']) * weight
            totals['expanded_basal_pi_m2'] += Fraction(fact['expanded_basal_pi_m2']) * weight
            for key, value in fact['expanded_by_10cm_class'].items():
                totals['classes'][key] += Fraction(value) * weight
            totals['visits'].append(fact['visit'])
        else:
            # Visited zero plots stay in visited_ha; inaccessible/not recorded
            # plots stay in the separate unobserved area.
            totals['unobserved_ha'] += area
    return totals


def _report(level, name, totals, mode, bounds=None):
    visited = totals['visited_ha']
    report = {'level': level, 'name': name, 'mode': mode,
              'design_ha': scalar(totals['design_ha']),
              'visited_ha': scalar(visited),
              'unobserved_ha': scalar(totals['unobserved_ha']),
              'trees_per_visited_ha': None,
              'basal_pi_m2_per_visited_ha': None,
              'class_trees_per_visited_ha': {},
              'visits': sorted(v for v in totals['visits'] if v is not None)}
    if mode == 'exact' and visited > 0:
        report['trees_per_visited_ha'] = scalar(totals['expanded_trees'] / visited)
        report['basal_pi_m2_per_visited_ha'] = scalar(totals['expanded_basal_pi_m2'] / visited)
        report['class_trees_per_visited_ha'] = {
            key: scalar(value / visited) for key, value in sorted(totals['classes'].items())}
    if bounds is not None:
        report['supported_range_ha'] = {
            'design_ha_min': scalar(bounds['design_lo']),
            'design_ha_max': scalar(bounds['design_hi']),
            'visited_ha_min': scalar(bounds['visited_lo']),
            'visited_ha_max': scalar(bounds['visited_hi']),
            'unobserved_ha_min': scalar(bounds['unobserved_lo']),
            'unobserved_ha_max': scalar(bounds['unobserved_hi'])}
    return report


def _subarea_reports(assignments_by_sub, fact_index):
    reports = []
    for sub in sorted(assignments_by_sub):
        contributions = [(fact_index[token], share)
                         for token, share in sorted(assignments_by_sub[sub].items())]
        totals = _aggregate(contributions)
        # A single subarea has no internal overlap: every assignment is exact.
        reports.append(_report('subarea', sub, totals, 'exact'))
    return reports


def _connected_components(subs, edges):
    remaining, components = set(subs), []
    while remaining:
        seed = remaining.pop()
        stack, component = [seed], {seed}
        while stack:
            current = stack.pop()
            for key in edges:
                if current not in key:
                    continue
                neighbour = key[1] if key[0] == current else key[0]
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    return components


def _validate_region(region, members, assignments_by_sub, fact_index, edges):
    for sub in members:
        require(sub in assignments_by_sub, f'region {region} has subarea without memberships: {sub}')
    member_set = set(members)
    member_edges = {key: edge for key, edge in edges.items() if key[0] in member_set and key[1] in member_set}

    unproven = {key for key, edge in member_edges.items() if edge['kind'] == UNPROVEN}
    co_claims = defaultdict(list)
    for sub in members:
        for token in assignments_by_sub[sub]:
            co_claims[token].append(sub)

    for token, claimers in sorted(co_claims.items()):
        if len(claimers) < 2:
            continue
        pairs = {_edge_key(a, b) for i, a in enumerate(claimers) for b in claimers[i + 1:]}
        for key in pairs:
            require(key in member_edges, f'overlap without declared management boundary: {token}')
            require(key not in unproven, f'unproven edge touches co-claimed fact: {token}')
        # Pairwise overlaps only: three subareas on one original fact cannot be
        # reconciled with pairwise shared evidence.
        require(len(claimers) == 2, f'fact claimed by three subareas is unsupported: {token}')
        a, b = claimers
        alpha_a = assignments_by_sub[a][token]
        alpha_b = assignments_by_sub[b][token]
        overlap = alpha_a + alpha_b - 1
        declared = member_edges[_edge_key(a, b)]['shared'].get(token)
        if overlap <= 0:
            # Direct shares fit inside the original fact: nothing is double
            # counted, so no shared evidence may be attached.
            require(declared is None, f'non-overlapping fact listed as shared: {token}')
            continue
        require(declared == overlap,
                f'shared fraction must equal direct overlap (a+b-1): {token}')
    return unproven, member_edges


def _exact_region(region, members, assignments_by_sub, fact_index, member_edges):
    union_weight = defaultdict(Fraction)
    for sub in members:
        for token, share in assignments_by_sub[sub].items():
            union_weight[token] += share
    for key, edge in member_edges.items():
        for token, shared in edge['shared'].items():
            union_weight[token] -= shared
    for token, weight in union_weight.items():
        require(0 < weight <= 1, f'region {region} weight outside (0,1] for fact: {token}')
    contributions = [(fact_index[token], weight) for token, weight in sorted(union_weight.items())]
    return _report('region', region, _aggregate(contributions), 'exact')


def _bounded_region(region, members, assignments_by_sub, fact_index, unproven):
    # Without proven shared fractions the union area is only bounded. Tight
    # generic bounds come from each connected component of allowed overlaps:
    # max member area <= union area <= sum of member areas.
    totals = {'design_ha': Fraction(0), 'visited_ha': Fraction(0), 'unobserved_ha': Fraction(0),
              'expanded_trees': Fraction(0), 'expanded_basal_pi_m2': Fraction(0),
              'classes': defaultdict(Fraction), 'visits': []}
    bounds = {'design_lo': Fraction(0), 'design_hi': Fraction(0),
              'visited_lo': Fraction(0), 'visited_hi': Fraction(0),
              'unobserved_lo': Fraction(0), 'unobserved_hi': Fraction(0)}
    for component in _connected_components(members, unproven):
        subtotals = [_aggregate([(fact_index[token], share)
                                 for token, share in sorted(assignments_by_sub[sub].items())])
                     for sub in sorted(component)]
        for field in ('design_ha', 'visited_ha', 'unobserved_ha'):
            values = [item[field] for item in subtotals]
            bounds[field.replace('_ha', '_lo')] += max(values)
            bounds[field.replace('_ha', '_hi')] += sum(values)
        # Densities and expanded counts cannot be recomputed without knowing
        # which shared piece is double counted, so they are withheld.
        totals['design_ha'] += bounds['design_hi']
        totals['visited_ha'] += bounds['visited_hi']
        totals['unobserved_ha'] += bounds['unobserved_hi']
    report = _report('region', region, totals, UNPROVEN, bounds)
    report['unproven_edges'] = sorted(f'{a}/{b}' for a, b in sorted(unproven))
    return report


def roll_management(facts, management):
    if not management:
        return None
    subareas, regions, membership_rows, edges = _parse_management(management)
    fact_index = _fact_index(facts)

    assignments_by_sub = {}
    for sub in subareas:
        rows = membership_rows.get(sub, [])
        require(rows, f'subarea without any membership: {sub}')
        assignments_by_sub[sub] = _load_assignments(sub, rows, fact_index)

    region_reports = []
    for region, row in sorted(regions.items()):
        members = row.get('subareas')
        require(isinstance(members, list) and members, f'region {region} needs subareas')
        require(len(members) == len(set(members)), f'duplicate subarea in region {region}')
        require(all(sub in assignments_by_sub for sub in members),
                f'region {region} references unknown subarea')
        unproven, member_edges = _validate_region(region, members, assignments_by_sub,
                                                  fact_index, edges)
        if unproven:
            region_reports.append(_bounded_region(region, members, assignments_by_sub,
                                                  fact_index, unproven))
        else:
            region_reports.append(_exact_region(region, members, assignments_by_sub,
                                                fact_index, member_edges))

    return {'subareas': _subarea_reports(assignments_by_sub, fact_index),
            'regions': region_reports}
