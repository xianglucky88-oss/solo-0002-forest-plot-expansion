"""Cross-edition identity reconciliation and revisit queue planning.

Inputs are two sealed evidence bundles (old and new editions) plus a separate
candidate overlay. A same-id record in the same visit is *proven* identity and
is never re-dispatched; overlay links are candidates only. Identity disputes are
solved as one-to-one bipartite pairings so that a single new record can never
count as the remeasurement of two old trees.
"""
from collections import defaultdict
from fractions import Fraction

from .common import number, require, scalar, unique
from .design import load_plots

MAX_PAIRINGS = 64


def cell_ref(cell):
    return {'plot': cell[0], 'cover': cell[1]}


def tree_ref(node):
    return {'visit': node[0], 'tree': node[1]}

# Keys that must never leave the statistics side: the field packet carries
# plots, identities and diameters only, never expansion weights or areas.
FORBIDDEN_FIELD_KEYS = frozenset({
    'weight', 'fraction', 'expansion_ha', 'represented_ha', 'design_ha',
    'visited_ha', 'unobserved_ha', 'expanded_trees', 'expanded_basal_pi_m2',
    'expanded_by_10cm_class', 'trees_per_visited_ha',
    'basal_pi_m2_per_visited_ha', 'class_trees_per_visited_ha',
})


def index_visits(doc):
    """Return visit id -> validated record, keeping every recorded tree."""
    visits = {}
    locations = {}
    for row in sorted(unique(doc['visits']).items()):
        vid, row = row
        state = row['status']
        require(state in ('visited', 'inaccessible', 'not_scheduled'),
                f'unknown status: {state}')
        cell = (row['plot'], row['cover'])
        require(cell not in locations, f'multiple visits for one plot-cover: {vid}')
        trees = unique(row['trees'])
        require(state == 'visited' or not trees, f'unvisited visit has trees: {vid}')
        records = {name: number(tree['dbh_cm'], 'diameter') for name, tree in trees.items()}
        visits[vid] = {'plot': row['plot'], 'cover': row['cover'],
                       'status': state, 'trees': records}
        locations[cell] = vid
    return visits, locations


class Editions:
    def __init__(self, old_doc, new_doc):
        require(old_doc['survey'] == new_doc['survey'], 'editions belong to different surveys')
        require(number(old_doc['min_dbh_cm'], 'minimum diameter')
                == number(new_doc['min_dbh_cm'], 'minimum diameter'),
                'editions use different minimum diameters')
        self.survey = old_doc['survey']
        self.min_dbh = number(old_doc['min_dbh_cm'], 'minimum diameter')
        self.old_doc, self.new_doc = old_doc, new_doc
        self.old_plots = load_plots(old_doc['plots'])
        self.new_plots = load_plots(new_doc['plots'])
        self.old_visits, self.old_locs = index_visits(old_doc)
        self.new_visits, self.new_locs = index_visits(new_doc)

    def cell(self, plots, plot, cover):
        district, _area, weight, shares = plots[plot]
        share = shares.get(cover, 0)
        require(share > 0, f'visit references unknown plot-cover: {plot}/{cover}')
        return district, weight * share

    def tree_record(self, side, visit_id, tree_id):
        visits = self.old_visits if side == 'old' else self.new_visits
        return visits[visit_id]['trees'][tree_id]


def validate_candidates(data, editions):
    require(isinstance(data, dict), 'candidate overlay must be an object')
    require(data.get('survey') == editions.survey,
            f"candidate survey {data.get('survey')!r} does not match {editions.survey!r}")
    proven_visit_ids = {r['visit'] for r in proven_visits(editions)}
    visit_links, tree_links = [], []
    for row in unique(data.get('visit_candidates', []), field='id').values():
        ov, nv = row['old_visit'], row['new_visit']
        require(ov in editions.old_visits, f'unknown old visit in candidate: {ov}')
        require(nv in editions.new_visits, f'unknown new visit in candidate: {nv}')
        old_cell = (editions.old_visits[ov]['plot'], editions.old_visits[ov]['cover'])
        new_cell = (editions.new_visits[nv]['plot'], editions.new_visits[nv]['cover'])
        require(old_cell == new_cell, f'visit candidate {row["id"]} relocates the field visit')
        visit_links.append({'candidate_id': row['id'], 'old_visit': ov,
                            'new_visit': nv, 'cell': cell_ref(old_cell),
                            'confirms_proven_identity': ov == nv and ov in proven_visit_ids})
    seen_pairs = set()
    for row in unique(data.get('tree_candidates', []), field='id').values():
        cid, ov, nv = row['id'], row['old_visit'], row['new_visit']
        ot, nt = row['old_tree'], row['new_tree']
        require(ov in editions.old_visits, f'unknown old visit in candidate: {ov}')
        require(nv in editions.new_visits, f'unknown new visit in candidate: {nv}')
        require(ot in editions.old_visits[ov]['trees'], f'unknown old tree in candidate {cid}: {ot}')
        require(nt in editions.new_visits[nv]['trees'], f'unknown new tree in candidate {cid}: {nt}')
        old_cell = (editions.old_visits[ov]['plot'], editions.old_visits[ov]['cover'])
        new_cell = (editions.new_visits[nv]['plot'], editions.new_visits[nv]['cover'])
        require(old_cell == new_cell, f'tree candidate {cid} relocates the tree to another cell')
        pair = (ov, ot, nv, nt)
        require(pair not in seen_pairs, f'duplicate candidate pair: {cid}')
        seen_pairs.add(pair)
        tree_links.append({'candidate_id': cid, 'old_visit': ov, 'old_tree': ot,
                           'new_visit': nv, 'new_tree': nt, 'cell': old_cell})
    return visit_links, tree_links


def proven_visits(editions):
    """Same visit id logged at the same plot-cover in both editions."""
    proven = []
    for vid, old in editions.old_visits.items():
        new = editions.new_visits.get(vid)
        if new is None:
            continue
        old_cell = (old['plot'], old['cover'])
        new_cell = (new['plot'], new['cover'])
        require(old_cell == new_cell, f'visit {vid} moved between plot-covers across editions')
        proven.append({'visit': vid, 'plot': old_cell[0], 'cover': old_cell[1]})
    return sorted(proven, key=lambda r: r['visit'])


def resolve_visit_identity(editions, visit_links):
    """Map old visit id -> new visit id.

    Proven same-id visits map to themselves. An uncontested visit candidate
    across different ids is accepted as the working identity hypothesis so
    that tree identity can follow the renumbered visit; it still appears in the
    candidate list and therefore remains distinguishable from a proven link.
    Validation has already ruled out relocations, duplicate cell visits and
    collisions with proven visits.
    """
    mapping = {vid: vid for vid in (r['visit'] for r in proven_visits(editions))}
    for link in visit_links:
        if not link['confirms_proven_identity']:
            mapping[link['old_visit']] = link['new_visit']
    return mapping


def build_components(editions, tree_links, visit_map):
    """Group tree identity edges (proven + candidate) into connected components."""
    edges = []
    for old_vid, new_vid in sorted(visit_map.items()):
        for tree_id in sorted(editions.old_visits[old_vid]['trees']):
            if tree_id in editions.new_visits[new_vid]['trees']:
                edges.append({'kind': 'proven', 'candidate_id': None,
                              'old': (old_vid, tree_id), 'new': (new_vid, tree_id),
                              'cell': (editions.old_visits[old_vid]['plot'],
                                       editions.old_visits[old_vid]['cover'])})
    for link in tree_links:
        edges.append({'kind': 'candidate', 'candidate_id': link['candidate_id'],
                      'old': (link['old_visit'], link['old_tree']),
                      'new': (link['new_visit'], link['new_tree']),
                      'cell': link['cell']})
    adjacency = defaultdict(set)
    for i, edge in enumerate(edges):
        adjacency[('o', edge['old'])].add(i)
        adjacency[('n', edge['new'])].add(i)
    components, visited_edges = [], set()
    for i in range(len(edges)):
        if i in visited_edges:
            continue
        stack, edge_ids = [i], set()
        while stack:
            j = stack.pop()
            if j in edge_ids:
                continue
            edge_ids.add(j)
            edge = edges[j]
            stack.extend(adjacency[('o', edge['old'])] - edge_ids)
            stack.extend(adjacency[('n', edge['new'])] - edge_ids)
        visited_edges |= edge_ids
        members = [edges[j] for j in sorted(edge_ids, key=lambda j: (edges[j]['old'], edges[j]['new']))]
        cells = {e['cell'] for e in members}
        require(len(cells) == 1, f'identity component spans multiple cells: {sorted(cells)}')
        components.append(members)
    return components


def enumerate_pairings(edges):
    """All one-to-one pairings of the component's bipartite identity graph."""
    by_old = defaultdict(list)
    for i, edge in enumerate(edges):
        by_old[edge['old']].append(i)
    old_nodes = sorted(by_old)
    results, chosen, used_new = [], set(), set()

    def backtrack(position):
        if len(results) >= MAX_PAIRINGS:
            return
        if position == len(old_nodes):
            results.append(frozenset(chosen))
            return
        backtrack(position + 1)  # this old tree stays unmatched
        for i in by_old[old_nodes[position]]:
            new_node = edges[i]['new']
            if new_node not in used_new:
                chosen.add(i)
                used_new.add(new_node)
                backtrack(position + 1)
                chosen.discard(i)
                used_new.discard(new_node)

    backtrack(0)
    return results, len(results) >= MAX_PAIRINGS


def describe_pairing(edges, pairing):
    all_old = {e['old'] for e in edges}
    all_new = {e['new'] for e in edges}
    matched_old = {edges[j]['old'] for j in pairing}
    matched_new = {edges[j]['new'] for j in pairing}
    overturned = [f"{edges[j]['old'][1]}->{edges[j]['new'][1]}"
                  for j in range(len(edges))
                  if edges[j]['kind'] == 'proven' and j not in pairing]
    return {
        'edges': sorted(f"{edges[j]['old'][1]}->{edges[j]['new'][1]}"
                        + ('' if edges[j]['kind'] == 'proven' else f"[{edges[j]['candidate_id']}]")
                        for j in sorted(pairing)),
        'unmatched_old_trees': sorted((tree_ref(n) for n in all_old - matched_old),
                                      key=lambda r: (r['visit'], r['tree'])),
        'unmatched_new_trees': sorted((tree_ref(n) for n in all_new - matched_new),
                                      key=lambda r: (r['visit'], r['tree'])),
        'overturns_proven_edges': sorted(overturned),
    }


def analyse_components(editions, components):
    """Split components into proven-only, candidate-only and disputed cases."""
    disputes, candidate_only, proven_count = [], [], 0
    for index, edges in enumerate(components, start=1):
        kinds = {e['kind'] for e in edges}
        degrees = defaultdict(int)
        for e in edges:
            degrees[('o', e['old'])] += 1
            degrees[('n', e['new'])] += 1
        contested = any(d > 1 for d in degrees.values())
        contradicted = (kinds == {'proven', 'candidate'})
        if not contradicted and not contested:
            if kinds == {'proven'}:
                proven_count += 1
            else:
                candidate_only.append(edges)
            continue
        pairings, truncated = enumerate_pairings(edges)
        default_set = frozenset(j for j, e in enumerate(edges) if e['kind'] == 'proven')
        default_index = next(i for i, pairing in enumerate(pairings) if pairing == default_set)
        rejected_claims = []
        for (side, node), degree in sorted(degrees.items()):
            if degree > 1:
                label = 'new' if side == 'n' else 'old'
                rejected_claims.append(
                    f'{label} record {node[1]} (visit {node[0]}) has {degree} identity claims; '
                    'a one-to-one pairing accepts at most one')
        cell = edges[0]['cell']
        _district, represented = editions.cell(editions.new_plots, *cell)
        disputes.append({
            'dispute_id': f'D-{index:03d}',
            'plot': cell[0], 'cover': cell[1],
            'candidate_ids': sorted({e['candidate_id'] for e in edges if e['candidate_id']}),
            'one_new_record_explains_two_old': any(
                d > 1 for (side, _), d in degrees.items() if side == 'n'),
            'rejected_double_remeasurement_claims': rejected_claims,
            'legal_pairings': [describe_pairing(edges, pairing) for pairing in pairings],
            'default_pairing_index': default_index,
            'pairing_enumeration_truncated': truncated,
            'represented_ha': scalar(represented),
            'edges': edges,
        })
    return disputes, candidate_only, proven_count


def coverage(editions):
    """Every new-edition plot-cover cell with its visit state and impact area."""
    rows = []
    for plot, (district, _area, weight, shares) in sorted(editions.new_plots.items()):
        for cover, share in sorted(shares.items()):
            if share <= 0:
                continue
            vid = editions.new_locs.get((plot, cover))
            new_status = editions.new_visits[vid]['status'] if vid else 'not_recorded'
            old_vid = editions.old_locs.get((plot, cover))
            old_status = editions.old_visits[old_vid]['status'] if old_vid else 'not_recorded'
            represented = weight * share
            rows.append({'plot': plot, 'cover': cover, 'district': district,
                         'visit': vid, 'old_visit': old_vid,
                         'old_status': old_status, 'new_status': new_status,
                         'observed_for_density': new_status == 'visited',
                         'represented_ha': scalar(represented),
                         '_area': represented})
    return rows


def diameter_list(editions, side, nodes):
    out = []
    for visit_id, tree_id in sorted(nodes):
        out.append({'visit': visit_id, 'tree': tree_id,
                    'dbh_cm': scalar(editions.tree_record(side, visit_id, tree_id))})
    return out


def build_tasks(editions, disputes, candidate_only, coverage_rows):
    tasks = []
    for dispute in disputes:
        cell = (dispute['plot'], dispute['cover'])
        edges = dispute['edges']
        old_nodes = {e['old'] for e in edges}
        new_nodes = {e['new'] for e in edges}
        visit_id = editions.new_locs.get(cell)
        tasks.append({
            'reason': 'identity_dispute', 'cell': cell,
            'visit': visit_id, 'status': editions.new_visits[visit_id]['status'],
            'dispute_id': dispute['dispute_id'],
            'candidate_ids': dispute['candidate_ids'],
            'old_trees': diameter_list(editions, 'old', old_nodes),
            'new_trees': diameter_list(editions, 'new', new_nodes),
            'area': Fraction(dispute['represented_ha']),
        })
    for edges in candidate_only:
        cell = edges[0]['cell']
        visit_id = editions.new_locs.get(cell)
        tasks.append({
            'reason': 'candidate_verify', 'cell': cell, 'visit': visit_id,
            'status': editions.new_visits[visit_id]['status'],
            'dispute_id': None,
            'candidate_ids': sorted({e['candidate_id'] for e in edges}),
            'old_trees': diameter_list(editions, 'old', {e['old'] for e in edges}),
            'new_trees': diameter_list(editions, 'new', {e['new'] for e in edges}),
            'area': editions.cell(editions.new_plots, *cell)[1],
        })
    for row in coverage_rows:
        if row['observed_for_density']:
            continue
        cell = (row['plot'], row['cover'])
        old_vid = row['old_visit']
        old_nodes = {(old_vid, t) for t in editions.old_visits[old_vid]['trees']} if old_vid else set()
        tasks.append({
            'reason': 'coverage_gap', 'cell': cell, 'visit': row['visit'],
            'status': row['new_status'], 'dispute_id': None, 'candidate_ids': [],
            'old_trees': diameter_list(editions, 'old', old_nodes),
            'new_trees': [], 'area': row['_area'],
        })
    category_rank = {'identity_dispute': 0, 'candidate_verify': 1, 'coverage_gap': 2}
    tasks.sort(key=lambda t: (-t['area'], category_rank[t['reason']], t['cell'][0], t['cell'][1]))
    for rank, task in enumerate(tasks, start=1):
        task['task_id'] = f'R-{rank:03d}'
    return tasks


def assert_field_safe(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            require(key not in FORBIDDEN_FIELD_KEYS and not str(key).endswith('_ha'),
                    f'weight/area field leaks into field packet: {key}')
            assert_field_safe(value)
    elif isinstance(obj, list):
        for value in obj:
            assert_field_safe(value)


def field_packet(editions, tasks):
    packet = {
        'packet': 'field_revisit_queue', 'survey': editions.survey,
        'min_dbh_cm': scalar(editions.min_dbh),
        'tasks': [{
            'task_id': t['task_id'], 'reason': t['reason'],
            'plot': t['cell'][0], 'cover': t['cell'][1],
            'visit': t['visit'], 'status': t['status'],
            'dispute_id': t['dispute_id'], 'candidate_ids': t['candidate_ids'],
            'old_trees': t['old_trees'], 'new_trees': t['new_trees'],
        } for t in tasks],
    }
    assert_field_safe(packet)
    return packet


def priority_effect(editions, dispute):
    """Whether alternative legal pairings change the revisit queue's priority.

    Every pairing stays inside one plot-cover cell, so the impact area can never
    move and the dispute generates exactly one verification task regardless of
    which legal pairing is chosen. Pairings only change the tag labels the crew
    must resolve within that single visit. Queue priority therefore never
    changes between legal pairings; differing workloads are reported separately.
    """
    pairings = dispute['legal_pairings']
    workloads = sorted({len(p['unmatched_old_trees']) + len(p['unmatched_new_trees'])
                        for p in pairings})
    return {
        'priority_changes_between_legal_pairings': False,
        'unmatched_record_counts': workloads,
        'basis': ('every legal pairing stays inside one plot-cover cell with the same '
                  f"represented area ({dispute['represented_ha']} ha) and yields exactly "
                  'one verification task, so the task stays at the same queue rank; '
                  'pairings differ only in the tag labels resolved during that visit'),
    }


def unmatched_records(editions, components):
    """Old/new tree nodes not covered by any proven or candidate edge."""
    linked_old, linked_new = set(), set()
    for edges in components:
        for edge in edges:
            linked_old.add(edge['old'])
            linked_new.add(edge['new'])
    old_all, new_all = set(), set()
    for old_vid, visit in editions.old_visits.items():
        old_all |= {(old_vid, t) for t in visit['trees']}
    for new_vid, visit in editions.new_visits.items():
        new_all |= {(new_vid, t) for t in visit['trees']}
    return {
        'old_trees_without_new_record': sorted(
            (tree_ref(n) for n in old_all - linked_old),
            key=lambda r: (r['visit'], r['tree'])),
        'new_trees_without_old_record': sorted(
            (tree_ref(n) for n in new_all - linked_new),
            key=lambda r: (r['visit'], r['tree'])),
    }


def statistics_packet(editions, visit_links, proven_visit_rows, components,
                      disputes, candidate_only, proven_tree_components,
                      coverage_rows, tasks, district_rows):
    proven_tree_rows = []
    for edges in components:
        if {e['kind'] for e in edges} == {'proven'}:
            for e in edges:
                proven_tree_rows.append({'visit': e['old'][0], 'tree': e['old'][1]})
    proven_tree_rows.sort(key=lambda r: (r['visit'], r['tree']))
    candidate_only_rows = [
        {'candidate_ids': sorted({e['candidate_id'] for e in edges}),
         'plot': edges[0]['cell'][0], 'cover': edges[0]['cell'][1],
         'old_trees': [tree_ref(e['old']) for e in edges],
         'new_trees': [tree_ref(e['new']) for e in edges]}
        for edges in candidate_only]
    orphan_rows = unmatched_records(editions, components)
    task_index = [{
        'task_id': t['task_id'], 'reason': t['reason'],
        'plot': t['cell'][0], 'cover': t['cell'][1], 'status': t['status'],
        'represented_ha': scalar(t['area']),
        'priority_rank': int(t['task_id'].split('-')[1]),
        'priority_reason': f"ranked by new-edition represented area ({scalar(t['area'])} ha); "
        + ('blocking identity dispute' if t['reason'] == 'identity_dispute'
           else 'candidate link awaiting field confirmation' if t['reason'] == 'candidate_verify'
           else f"not observed for density: {t['status']}"),
        'dispute_id': t['dispute_id'], 'candidate_ids': t['candidate_ids'],
    } for t in tasks]
    dispute_rows = [{
        'dispute_id': d['dispute_id'], 'plot': d['plot'], 'cover': d['cover'],
        'represented_ha': d['represented_ha'], 'candidate_ids': d['candidate_ids'],
        'one_new_record_explains_two_old': d['one_new_record_explains_two_old'],
        'rejected_double_remeasurement_claims': d['rejected_double_remeasurement_claims'],
        'legal_pairings': d['legal_pairings'],
        'default_pairing_index': d['default_pairing_index'],
        'pairing_enumeration_truncated': d['pairing_enumeration_truncated'],
        'priority_effect': priority_effect(editions, d),
    } for d in disputes]
    representativeness = [{
        'district': r['district'], 'cover': r['cover'],
        'design_ha': r['design_ha'], 'visited_ha': r['visited_ha'],
        'unobserved_ha': r['unobserved_ha'],
        'density_publishable': r['trees_per_visited_ha'] is not None,
        'blocking_reason': None if r['trees_per_visited_ha'] is not None
        else 'no visited support: visited zero-plot and inaccessible/unrecorded are distinct states',
    } for r in district_rows]
    return {
        'packet': 'statistician_review', 'survey': editions.survey,
        'min_dbh_cm': scalar(editions.min_dbh),
        'coverage_cells': [{k: v for k, v in row.items() if k != '_area'} for row in coverage_rows],
        'identity': {
            'proven_visits': proven_visit_rows,
            'proven_trees': proven_tree_rows,
            'visit_candidates': visit_links,
            'candidate_only_links': candidate_only_rows,
            'unmatched_records': orphan_rows,
            'deduped_without_redispatch': {
                'proven_visits': len(proven_visit_rows),
                'proven_tree_components': proven_tree_components,
                'proven_trees': len(proven_tree_rows),
            },
        },
        'blocking_disputes': dispute_rows,
        'representativeness': representativeness,
        'task_queue': task_index,
        'cross_check': {
            'field_task_ids': [t['task_id'] for t in tasks],
            'common_id_note': 'task_id, plot, cover and visit ids are shared with the '
                              'field packet; weights and areas exist in this packet only',
        },
    }


def reconcile(old_doc, new_doc, candidate_doc, district_rows):
    """Full pipeline returning (field_packet, statistics_packet)."""
    editions = Editions(old_doc, new_doc)
    visit_links, tree_links = validate_candidates(candidate_doc, editions)
    proven_visit_rows = proven_visits(editions)
    visit_map = resolve_visit_identity(editions, visit_links)
    components = build_components(editions, tree_links, visit_map)
    disputes, candidate_only, proven_tree_components = analyse_components(editions, components)
    coverage_rows = coverage(editions)
    tasks = build_tasks(editions, disputes, candidate_only, coverage_rows)
    return (field_packet(editions, tasks),
            statistics_packet(editions, visit_links, proven_visit_rows, components,
                              disputes, candidate_only, proven_tree_components,
                              coverage_rows, tasks, district_rows))
