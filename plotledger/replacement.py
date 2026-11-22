"""Surveyor-listed replacement points for inaccessible plot-covers.

A replacement roster is optional. When absent, the pipeline keeps its legacy
behaviour exactly. When present, each candidate point may support the covers of
one single unsupported plot-cover of the same design domain (district), the
summed replacement area for a plot-cover must never exceed its originally
represented area, and directly visited results are never touched.
"""
from collections import Counter, defaultdict
from fractions import Fraction

from .common import integer, number, require, scalar, unique


def _location(row, label):
    value = row.get('location')
    require(isinstance(value, list) and len(value) == 2, f'{label} needs a [x, y] location')
    return (integer(value[0], f'{label} x'), integer(value[1], f'{label} y'))


def plot_locations(doc):
    locations = {}
    for row in unique(doc['plots']).values():
        locations[row['id']] = _location(row, f"plot {row['id']}")
    return locations


def load_roster(rows, districts, min_dbh):
    require(isinstance(rows, list) and rows, 'replacement roster must be a non-empty list')
    candidates = {}
    visit_ids = set()
    for entry in rows:
        cid = entry['id']
        require(isinstance(cid, str) and cid.strip() and cid not in candidates,
                f'invalid or duplicate candidate id: {cid!r}')
        district = entry['district']
        require(isinstance(district, str) and district in districts,
                f'candidate outside known design domain: {cid}')
        area = number(entry['plot_m2'], 'candidate plot area')
        weight = number(entry['expansion_ha'], 'candidate expansion area')
        require(area > 0 and weight > 0, f'nonpositive candidate geometry or weight: {cid}')
        require(type(entry.get('public_direct')) is bool, f'public_direct must be boolean: {cid}')
        require(isinstance(entry.get('visits'), list) and entry['visits'],
                f'candidate needs a non-empty visits list: {cid}')
        loc = _location(entry, f'candidate {cid}')
        unit = weight / (area / 10_000)
        covers = {}
        fraction_total = Fraction(0)
        for visit in unique(entry['visits'], field='id').values():
            require(visit['id'] not in visit_ids,
                    f"duplicate candidate visit id across roster: {visit['id']}")
            visit_ids.add(visit['id'])
            cover = visit['cover']
            require(isinstance(cover, str) and cover.strip(), f'blank cover on candidate: {cid}')
            require(cover not in covers, f'duplicate candidate cover visit: {cid} {cover}')
            fraction = number(visit['fraction'], 'candidate segment fraction') \
                if 'fraction' in visit else Fraction(1)
            fraction_total += fraction
            state = visit['status']
            require(state in ('visited', 'inaccessible', 'not_scheduled'),
                    f'unknown candidate visit status: {state}')
            trees = unique(visit.get('trees', []))
            require(state == 'visited' or not trees, f'unvisited candidate has trees: {cid}')
            admitted = {name: number(tree['dbh_cm'], 'diameter') for name, tree in trees.items()}
            admitted = {name: d for name, d in admitted.items() if d >= min_dbh}
            classes = Counter()
            for diameter in admitted.values():
                classes[str((diameter // 10) * 10)] += unit
            basal = (unit * sum(d * d for d in admitted.values()) / 40_000
                     if state == 'visited' else None)
            covers[cover] = {
                'visit': visit['id'], 'status': state, 'fraction': fraction,
                'represented_ha': weight * fraction, 'unit': unit,
                'admitted_trees': sorted(admitted),
                'expanded_trees': scalar(unit * len(admitted)) if state == 'visited' else None,
                'expanded_basal_pi_m2': scalar(basal) if basal is not None else None,
                'expanded_by_10cm_class': {k: scalar(v) for k, v in sorted(classes.items())},
            }
        require(fraction_total == 1, f'candidate fractions must add to one: {cid}')
        candidates[cid] = {'id': cid, 'district': district, 'location': loc,
                           'public_direct': entry['public_direct'], 'weight': weight,
                           'covers': covers}
    return candidates


def normalized_roster(candidates):
    out = []
    for cid in sorted(candidates):
        candidate = candidates[cid]
        out.append({
            'id': cid, 'district': candidate['district'],
            'location': list(candidate['location']),
            'public_direct': candidate['public_direct'],
            'visits': [{
                'visit': detail['visit'], 'cover': cover, 'status': detail['status'],
                'represented_ha': scalar(detail['represented_ha']),
                'admitted_trees': detail['admitted_trees'],
                'expanded_trees': detail['expanded_trees'],
                'expanded_basal_pi_m2': detail['expanded_basal_pi_m2'],
                'expanded_by_10cm_class': detail['expanded_by_10cm_class'],
            } for cover, detail in sorted(candidate['covers'].items())],
        })
    return out


def _contribution(candidate, cover):
    detail = candidate['covers'][cover]
    return {'candidate': candidate['id'], 'visit': detail['visit'], 'cover': cover,
            'represented_ha': detail['represented_ha'],
            'expanded_trees': Fraction(detail['expanded_trees']) if detail['expanded_trees'] is not None else Fraction(0),
            'expanded_basal_pi_m2': Fraction(detail['expanded_basal_pi_m2'])
            if detail['expanded_basal_pi_m2'] is not None else Fraction(0),
            'expanded_by_10cm_class': {k: Fraction(v) for k, v in detail['expanded_by_10cm_class'].items()}}


def _feasible_lists(targets, candidates):
    """Public, visited, same-domain candidate covers that fit each target alone."""
    feasible = []
    for target in targets:
        options = []
        for cid in sorted(candidates):
            candidate = candidates[cid]
            if not candidate['public_direct'] or candidate['district'] != target['district']:
                continue
            detail = candidate['covers'].get(target['cover'])
            if detail is None or detail['status'] != 'visited':
                continue
            if detail['represented_ha'] <= target['demand']:
                options.append((cid, detail['represented_ha']))
        feasible.append(sorted(options, key=lambda item: (item[1], item[0])))
    return feasible


def _subsets(options, used, remaining):
    choices = []

    def recurse(index, chosen, total):
        if index == len(options):
            choices.append(tuple(chosen))
            return
        recurse(index + 1, chosen, total)
        cid, area = options[index]
        if cid not in used and total + area <= remaining:
            recurse(index + 1, tuple(chosen) + (cid,), total + area)

    recurse(0, (), Fraction(0))
    return choices


def _greedy(targets, candidates, locations, feasible):
    used = set()
    assignments = []
    for index, target in enumerate(targets):
        choices = [(cid, area) for cid, area in feasible[index] if cid not in used]
        if not choices:
            continue
        tx, ty = locations[target['plot']]

        def rank(item):
            cid, area = item
            cx, cy = candidates[cid]['location']
            return ((cx - tx) ** 2 + (cy - ty) ** 2, cid)

        cid, area = min(choices, key=rank)
        used.add(cid)
        assignments.append({'plot': target['plot'], 'cover': target['cover'],
                            'candidate': cid, 'visit': candidates[cid]['covers'][target['cover']]['visit'],
                            'represented_ha': scalar(area)})
    total = sum((Fraction(a['represented_ha']) for a in assignments), Fraction(0))
    return {'total_ha': scalar(total), 'assignments': assignments}


def solve(facts, candidates, locations):
    targets = []
    for fact in facts:
        if fact['status'] != 'visited':
            targets.append({'plot': fact['plot'], 'district': fact['district'],
                            'cover': fact['cover'], 'demand': Fraction(fact['represented_ha'])})
    targets.sort(key=lambda t: (t['plot'], t['cover']))
    feasible = _feasible_lists(targets, candidates)

    direct_area = defaultdict(Fraction)
    direct_basal = defaultdict(Fraction)
    for fact in facts:
        if fact['status'] == 'visited':
            key = fact['district'], fact['cover']
            direct_area[key] += Fraction(fact['represented_ha'])
            direct_basal[key] += Fraction(fact['expanded_basal_pi_m2'])

    groups = sorted({(fact['district'], fact['cover']) for fact in facts})
    extrema = {key: {'min_density': None, 'max_density': None,
                     'min_replacement_ha': None, 'max_replacement_ha': None,
                     'min_replacement_density': None, 'max_replacement_density': None}
               for key in groups}

    best_total = Fraction(-1)
    best_count = 0
    canonical = None
    chosen_by_target = [()] * len(targets)

    def bound(index, used, accumulated):
        reachable = accumulated
        for later in range(index, len(targets)):
            spare = sum((area for cid, area in feasible[later] if cid not in used), Fraction(0))
            reachable += min(targets[later]['demand'], spare)
        return reachable

    def leaf(accumulated):
        nonlocal best_total, best_count, canonical
        replacement_area = defaultdict(Fraction)
        replacement_basal = defaultdict(Fraction)
        signature = []
        for index, target in enumerate(targets):
            signature.append(tuple(chosen_by_target[index]))
            for cid in chosen_by_target[index]:
                contribution = _contribution(candidates[cid], target['cover'])
                key = target['district'], target['cover']
                replacement_area[key] += contribution['represented_ha']
                replacement_basal[key] += contribution['expanded_basal_pi_m2']
        if accumulated > best_total:
            best_total = accumulated
            best_count = 1
            canonical = [tuple(part) for part in signature]
            for key in groups:
                extrema[key]['min_replacement_ha'] = extrema[key]['max_replacement_ha'] = replacement_area[key]
                denominator = direct_area[key] + replacement_area[key]
                density = ((direct_basal[key] + replacement_basal[key]) / denominator
                           if denominator else None)
                extrema[key]['min_density'] = extrema[key]['max_density'] = density
                extrema[key]['min_replacement_density'] = extrema[key]['max_replacement_density'] = (
                    replacement_basal[key] / replacement_area[key] if replacement_area[key] else None)
        elif accumulated == best_total:
            best_count += 1
            if canonical is None or signature < canonical:
                canonical = [tuple(part) for part in signature]
            for key in groups:
                extrema[key]['min_replacement_ha'] = min(extrema[key]['min_replacement_ha'],
                                                          replacement_area[key])
                extrema[key]['max_replacement_ha'] = max(extrema[key]['max_replacement_ha'],
                                                          replacement_area[key])
                denominator = direct_area[key] + replacement_area[key]
                density = ((direct_basal[key] + replacement_basal[key]) / denominator
                           if denominator else None)
                if density is not None:
                    if extrema[key]['min_density'] is None or density < extrema[key]['min_density']:
                        extrema[key]['min_density'] = density
                    if extrema[key]['max_density'] is None or density > extrema[key]['max_density']:
                        extrema[key]['max_density'] = density
                replacement_density = (replacement_basal[key] / replacement_area[key]
                                        if replacement_area[key] else None)
                if replacement_density is not None:
                    if (extrema[key]['min_replacement_density'] is None
                            or replacement_density < extrema[key]['min_replacement_density']):
                        extrema[key]['min_replacement_density'] = replacement_density
                    if (extrema[key]['max_replacement_density'] is None
                            or replacement_density > extrema[key]['max_replacement_density']):
                        extrema[key]['max_replacement_density'] = replacement_density

    def search(index, used, accumulated):
        if index == len(targets):
            leaf(accumulated)
            return
        if bound(index, used, accumulated) < best_total:
            return
        remaining = targets[index]['demand']
        for choice in _subsets(feasible[index], used, remaining):
            chosen_by_target[index] = choice
            added = sum((area for cid, area in feasible[index] if cid in choice), Fraction(0))
            search(index + 1, used | set(choice), accumulated + added)
        chosen_by_target[index] = ()

    search(0, set(), Fraction(0))

    assignments = []
    assigned = set()
    canonical_lookup = {}
    for index, target in enumerate(targets):
        for cid in canonical[index]:
            contribution = _contribution(candidates[cid], target['cover'])
            assigned.add(cid)
            entry = {'plot': target['plot'], 'cover': target['cover'], 'candidate': cid,
                     'visit': contribution['visit'], 'represented_ha': scalar(contribution['represented_ha'])}
            assignments.append(entry)
            canonical_lookup.setdefault((target['plot'], target['cover']), []).append(contribution)

    unassigned = []
    for cid in sorted(candidates):
        candidate = candidates[cid]
        if not candidate['public_direct']:
            reason = 'not_public'
        elif cid not in assigned:
            reason = 'no_feasible_target'
        else:
            continue
        unassigned.append({'candidate': cid, 'reason': reason})

    total_demand = sum((t['demand'] for t in targets), Fraction(0))
    solution = {
        'eligible_targets': [{'plot': t['plot'], 'cover': t['cover'], 'district': t['district'],
                              'demand_ha': scalar(t['demand'])} for t in targets],
        'canonical': {'total_ha': scalar(best_total), 'optimal_allocation_count': best_count,
                      'assignments': assignments},
        'nearest_greedy': _greedy(targets, candidates, locations, feasible),
        'lost_ha_vs_demand': scalar(total_demand - best_total),
        'unassigned_candidates': unassigned,
    }
    return solution, canonical_lookup, extrema, best_count
