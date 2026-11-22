"""Reconcile field re-checks of stem numbering against the delivered single-stem census.

Identity is resolved only from explicit certified statements and ambiguity options.
Row order and diameter proximity are never used as evidence.

Certified statement vocabulary:
  duplicate  - a repeated reading of an old stem; contributes no further stem
  rename     - one old stem re-numbered to one review stem
  split      - one old stem continues as review_stem, with extra stems of the same tree
  merge      - several old stems resolved to one current stem (one tree)
  new_tree   - a review stem with no old anchor

Each admissible review stem must be covered by exactly one statement. A review stem
with ``dbh_cm: null`` is an identity record without a diameter: the tree still counts,
but the basal area of its component is not complete.
"""
from collections import defaultdict
from fractions import Fraction
from itertools import product

from .census import expansion_unit
from .common import number, require, scalar, unique

_DUPLICATE = 'duplicate'
_RENAME = 'rename'
_SPLIT = 'split'
_MERGE = 'merge'
_NEW = 'new_tree'
_TYPES = (_DUPLICATE, _RENAME, _SPLIT, _MERGE, _NEW)
_OLD_TYPES = (_DUPLICATE, _RENAME, _SPLIT, _MERGE)


class _UnionFind:
    def __init__(self, nodes):
        self.parent = {node: node for node in nodes}

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, left, right):
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def _statement(stmt, review_ids, old_ids, allow_old_reuse, seen_old):
    require(isinstance(stmt, dict), 'identity statement must be an object')
    kind = stmt.get('type')
    require(kind in _TYPES, f'unknown identity statement type: {kind!r}')
    review = stmt.get('review_stem')
    require(review in review_ids, f'statement references unknown review stem: {review!r}')
    old = ()
    if kind in (_DUPLICATE, _RENAME, _SPLIT):
        anchor = stmt.get('old_stem')
        require(anchor in old_ids, f'statement references unknown old stem: {anchor!r}')
        old = (anchor,)
    elif kind == _MERGE:
        anchors = stmt.get('old_stems')
        require(isinstance(anchors, list) and len(anchors) >= 2, 'merge needs at least two old_stems')
        require(len(set(anchors)) == len(anchors), f'repeated old stem in merge: {review!r}')
        require(all(anchor in old_ids for anchor in anchors), f'merge references unknown old stem: {review!r}')
        old = tuple(anchors)
    if kind in _OLD_TYPES:
        if not allow_old_reuse:
            require(not any(anchor in seen_old for anchor in old),
                    f'certified and ambiguous statements conflict on old stem: {old}')
        seen_old.update(old)
    added = ()
    if kind == _SPLIT:
        extra = stmt.get('added_review_stems')
        require(isinstance(extra, list) and extra, 'split needs non-empty added_review_stems')
        require(len(set(extra)) == len(extra), f'repeated added stem in split: {review!r}')
        require(all(step in review_ids for step in extra), f'split references unknown review stem: {review!r}')
        require(review not in extra, f'split continuing stem also listed as added: {review!r}')
        added = tuple(extra)
    require(not (kind == _NEW and ('old_stem' in stmt or 'old_stems' in stmt)),
            f'new_tree statement cannot name an old stem: {review!r}')
    return kind, review, old, added


def _load_review(row, visit, old_dbh, min_dbh):
    review_id = row['id']
    require(visit['status'] == 'visited', f'review on non-visited visit: {review_id}')
    stems = unique(row.get('stems', []))
    measured, readings = {}, {}
    for stem_id, stem in stems.items():
        raw = stem.get('dbh_cm')
        if raw is None:
            readings[stem_id] = None
            continue
        diameter = number(raw, 'review diameter')
        require(diameter >= min_dbh, f'sub-minimum measured review stem must be removed, not reconciled: {stem_id}')
        measured[stem_id] = diameter
        readings[stem_id] = diameter
    old_ids = set(old_dbh)
    review_ids = set(stems)

    covered = {}

    def claim(stem_id, origin):
        require(stem_id not in covered, f'review stem claimed by more than one statement: {stem_id}')
        covered[stem_id] = origin

    certified, certified_old = [], set()
    certified_rows = row.get('certified', [])
    require(isinstance(certified_rows, list), 'certified must be a list')
    for stmt in certified_rows:
        kind, review, old, added = _statement(stmt, review_ids, old_ids, True, certified_old)
        claim(review, 'certified')
        for step in added:
            claim(step, 'certified split')
        certified.append((kind, review, old, added))
    ambiguous_stems, ambiguous_old, groups = set(), set(), []
    ambiguity_rows = row.get('ambiguities', [])
    require(isinstance(ambiguity_rows, list), 'ambiguities must be a list')
    for group in ambiguity_rows:
        require(isinstance(group, dict), 'ambiguity must be an object')
        group_id = group['id']
        require(isinstance(group_id, str) and group_id.strip(), 'ambiguity needs a non-blank id')
        options = group.get('options')
        require(isinstance(options, list) and len(options) >= 2, f'ambiguity needs two options: {group_id}')
        labels, parsed, member_sets, group_old = set(), [], [], set()
        for option in options:
            label = option.get('label')
            require(isinstance(label, str) and label.strip() and label not in labels,
                    f'duplicate or blank ambiguity option label in {group_id}')
            labels.add(label)
            statements, members, option_old = [], set(), set()
            for stmt in option.get('statements', []):
                kind, review, old, added = _statement(stmt, review_ids, old_ids, False, option_old)
                require(all(anchor not in certified_old for anchor in old),
                        f'ambiguity reopens a certified old stem in {group_id}/{label}: {old}')
                require(all(anchor not in ambiguous_old for anchor in old),
                        f'old stem contested by another ambiguity group in {group_id}/{label}: {old}')
                require(review not in covered, f'review stem already settled by certified evidence: {review}')
                statements.append((kind, review, old, added))
                members.add(review)
                members.update(added)
            group_old.update(option_old)
            parsed.append((label, statements))
            member_sets.append(members)
        ambiguous_old.update(group_old)
        members = member_sets[0]
        require(members and all(step == members for step in member_sets[1:]),
                f'ambiguity options must cover the same review stems: {group_id}')
        require(members.isdisjoint(covered), f'ambiguity reopens a certified review stem: {group_id}')
        for step in members:
            claim(step, f'ambiguity {group_id}')
        ambiguous_stems.update(members)
        groups.append((group_id, parsed))
    for stem_id in review_ids:
        require(stem_id in covered, f'review stem without an explicit identity statement: {stem_id}')

    # Options must actually change the identity resolution; identical options cannot be a dispute.
    for group_id, parsed in groups:
        baseline = _scenarios(certified, old_dbh, readings, [[(parsed[0][0], parsed[0][1])]])[0][1]
        for label, statements in parsed[1:]:
            candidate = _scenarios(certified, old_dbh, readings, [[(label, statements)]])[0][1]
            require(_signature_map(candidate) != _signature_map(baseline),
                    f'ambiguity options do not distinguish any identity: {group_id}/{label}')
    return {'id': review_id, 'visit': row['visit'], 'old_dbh': old_dbh, 'readings': readings,
            'measured': measured, 'certified': certified, 'groups': groups}


def _scenarios(certified, old_dbh, readings, option_choices):
    scenarios = []
    for choice in product(*option_choices):
        extra = [stmt for _, statements in choice for stmt in statements]
        scenarios.append((choice, _build_components(certified + extra, old_dbh, readings)))
    return scenarios


def _build_components(statements, old_dbh, readings):
    review_ids = set(readings)
    old_ids = set(old_dbh)
    nodes = [('old', stem) for stem in old_ids] + [('rev', stem) for stem in review_ids]
    finder = _UnionFind(nodes)
    duplicates, slots = set(), {}
    for kind, review, old, added in statements:
        review_node = ('rev', review)
        if kind == _DUPLICATE:
            duplicates.add(review)
            continue
        if kind == _NEW:
            slots[review] = readings[review]
            continue
        for anchor in old:
            finder.union(review_node, ('old', anchor))
        slots[review] = readings[review]
        for step in added:
            finder.union(review_node, ('rev', step))
            slots[step] = readings[step]
    grouped = defaultdict(list)
    for node in nodes:
        if node[0] == 'rev' and node[1] in duplicates:
            continue
        grouped[finder.find(node)].append(node)
    components = {}
    for members in grouped.values():
        anchors = tuple(sorted(stem for kind, stem in members if kind == 'old'))
        review_stems = tuple(sorted(stem for kind, stem in members if kind == 'rev'))
        review_slots = [slots[stem] for stem in review_stems if stem in slots]
        if review_slots:
            current = review_slots
        else:
            current = [old_dbh[stem] for stem in anchors]
        missing = sum(1 for slot in current if slot is None)
        coefficient = sum(Fraction(slot) ** 2 for slot in current if slot is not None) / 40_000
        signature = anchors if anchors else (('new', review_stems),)
        components[signature] = {'old_anchors': list(anchors), 'review_stems': list(review_stems),
                                 'measured_basal_pi_m2_coeff': coefficient, 'missing_slots': missing,
                                 'trees': 1}
    return components


def _signature_map(components):
    return {signature: (payload['measured_basal_pi_m2_coeff'], payload['missing_slots'],
                        tuple(payload['review_stems'])) for signature, payload in components.items()}


def _is_new_signature(signature):
    return len(signature) == 1 and isinstance(signature[0], tuple) and signature[0][0] == 'new'


def _component_view(signature, payload):
    if _is_new_signature(signature):
        identity = {'new_review_stems': list(signature[0][1])}
    else:
        identity = {'old_anchors': list(signature), 'review_stems': payload['review_stems']}
    return {
        'identity': identity,
        'trees': 1,
        'measured_basal_pi_m2_coeff': scalar(payload['measured_basal_pi_m2_coeff']),
        'missing_slots': payload['missing_slots'],
    }


def reconcile(plots, visits, min_dbh, review_rows):
    visit_index = unique(visits)
    loaded, review_visits = [], set()
    for row in unique(review_rows).values():
        visit_id = row.get('visit')
        require(isinstance(visit_id, str) and visit_id in visit_index,
                f'review references unknown visit: {visit_id!r}')
        require(visit_id not in review_visits, f'multiple reviews for one visit: {visit_id}')
        review_visits.add(visit_id)
        visit = visit_index[visit_id]
        old_dbh = {}
        for tree in unique(visit['trees']).values():
            diameter = number(tree['dbh_cm'], 'delivered diameter')
            if diameter >= min_dbh:
                old_dbh[tree['id']] = diameter
        loaded.append((row, visit, _load_review(row, visit, old_dbh, min_dbh)))

    reports = []
    for row, visit, model in loaded:
        district, area, weight, shares = plots[visit['plot']]
        require(visit['cover'] in shares and shares[visit['cover']] > 0,
                f"review visit has no design share: {model['id']}")
        unit = expansion_unit(area, weight)
        readings = model['readings']
        option_choices = [[(label, statements) for label, statements in parsed]
                          for _, parsed in model['groups']]
        raw_scenarios = _scenarios(model['certified'], model['old_dbh'], readings, option_choices)
        signatures = [set(components) for _, components in raw_scenarios]
        stable_signatures = set.intersection(*signatures) if signatures else set(raw_scenarios[0][1])
        for signature in list(stable_signatures):
            payloads = {_signature_map(components)[signature] for _, components in raw_scenarios}
            if len(payloads) != 1:
                stable_signatures.discard(signature)
        stable_components = {signature: raw_scenarios[0][1][signature] for signature in stable_signatures}
        stable_trees = len(stable_components)
        stable_coeff = sum((payload['measured_basal_pi_m2_coeff'] for payload in stable_components.values()),
                           Fraction(0))
        stable_missing = sum(payload['missing_slots'] for payload in stable_components.values())
        duplicate_stems = sorted(stem for kind, stem, _, _ in model['certified'] if kind == _DUPLICATE)
        missing_stems = sorted(stem for stem, value in readings.items()
                               if value is None and stem not in duplicate_stems)

        scenarios = []
        for choice, components in raw_scenarios:
            trees = len(components)
            coefficient = sum((payload['measured_basal_pi_m2_coeff'] for payload in components.values()),
                              Fraction(0))
            scenarios.append({
                'choices': {group_id: label for (group_id, _), (label, _)
                            in zip(model['groups'], choice)},
                'trees': trees,
                'measured_basal_pi_m2_coeff': scalar(coefficient),
                'complete': all(payload['missing_slots'] == 0 for payload in components.values()),
                'components': [_component_view(signature, payload)
                               for signature, payload in sorted(components.items(), key=lambda item: str(item[0]))],
            })

        tree_options, basal_options = [], []
        for group_id, parsed in model['groups']:
            member_stems = set()
            for _, statements in parsed:
                for _, review, _, added in statements:
                    member_stems.add(review)
                    member_stems.update(added)
            for label, statements in parsed:
                components = _build_components(model['certified'] + statements, model['old_dbh'], readings)
                touched = [payload for payload in components.values()
                           if any(stem in member_stems for stem in payload['review_stems'])]
                coefficient = sum((payload['measured_basal_pi_m2_coeff'] for payload in touched), Fraction(0))
                tree_options.append({'ambiguity': group_id, 'option': label,
                                     'trees': len(touched),
                                     'expanded_trees': scalar(unit * len(touched))})
                basal_options.append({'ambiguity': group_id, 'option': label,
                                      'measured_basal_pi_m2_coeff': scalar(coefficient),
                                      'expanded_basal_pi_m2': scalar(unit * coefficient),
                                      'complete': all(payload['missing_slots'] == 0 for payload in touched)})

        delivered_trees = len(model['old_dbh'])
        delivered_coeff = sum((Fraction(diameter) ** 2 for diameter in model['old_dbh'].values()),
                              Fraction(0)) / 40_000
        disputed = bool(model['groups'])
        reports.append({
            'review': model['id'],
            'visit': row['visit'],
            'plot': visit['plot'],
            'district': district,
            'review_readings': {
                'measured': sorted(model['measured']),
                'unmeasured': sorted(stem for stem, value in readings.items()
                                     if value is None and stem not in duplicate_stems),
                'duplicate': duplicate_stems,
                'total_stems': len(readings),
            },
            'tree_reconciliation': {
                'delivered_trees': delivered_trees,
                'stable_trees': stable_trees,
                'stable_expanded_trees': scalar(unit * stable_trees),
                'disputed': disputed,
                'dispute_options': tree_options,
                'publishable': not disputed,
                'scenario_trees': sorted({item['trees'] for item in scenarios}),
            },
            'basal_reconciliation': {
                'delivered_basal_pi_m2_coeff': scalar(delivered_coeff),
                'stable_measured_basal_pi_m2_coeff': scalar(stable_coeff),
                'stable_expanded_basal_pi_m2': scalar(unit * stable_coeff),
                'stable_complete': stable_missing == 0,
                'missing_stems': missing_stems,
                'disputed': disputed,
                'dispute_options': basal_options,
                'publishable': not disputed and stable_missing == 0,
                'scenario_complete': sorted({item['complete'] for item in scenarios}),
            },
            'scenarios': scenarios,
        })
    return reports
