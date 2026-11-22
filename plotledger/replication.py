"""Delete-group replicate weights, domain uncertainty, and paired contrasts.

Every replicate group is one sampling-design number.  A group carries its own
expansion weights (one per plot) and its own variance coefficient.  Domain
numerators and denominators are rebuilt from the same visit / admission / area
facts as the full design, so every replicate is divided by its own visited
area.  A contrast is formed only inside one replicate number: a group that
supports just one of the two covers is reported as unpaired and is never
joined with a group that supports the other cover.

Coefficients are used exactly as declared.  When a domain is supported by only
part of the declared groups, the usable coefficient sum is reported so an
under-powered variance is visible rather than silently renormalized.
"""
from fractions import Fraction

from .census import default_weights, expand
from .common import number, require, scalar, sqrt_text

METRICS = ('trees_per_visited_ha', 'basal_pi_m2_per_visited_ha')

DESIGN_KEYS = ('replications', 'replicate_designs')


def _alias(row, names, label):
    present = [name for name in names if name in row]
    require(len(present) == 1, f'{label} needs exactly one of {", ".join(names)}')
    return row[present[0]]


def _weights(raw, plots, label):
    """One expansion weight per plot, from any of the accepted layouts."""
    if isinstance(raw, dict):
        require(set(raw) == set(plots), f'{label} weights must cover exactly the plot ids')
        pairs = sorted(raw.items())
    else:
        require(isinstance(raw, list) and raw, f'{label} weights must be an object or a list')
        if all(isinstance(item, dict) for item in raw):
            pairs = []
            for item in raw:
                plot = _alias(item, ('plot', 'id'), f'{label} weight entry')
                value = _alias(item, ('expansion_ha', 'weight'), f'{label} weight entry {plot}')
                pairs.append((plot, value))
            require(len({plot for plot, _ in pairs}) == len(pairs)
                    and {plot for plot, _ in pairs} == set(plots),
                    f'{label} weights must cover exactly the plot ids')
            pairs.sort()
        else:
            require(len(raw) == len(plots),
                    f'{label} weights must be a plot->weight object or a plot-aligned list')
            pairs = list(zip(sorted(plots), raw))
    weights = {}
    for plot, value in pairs:
        weights[plot] = number(value, f'{label} weight {plot}')
    return weights


def declared(doc):
    present = [key for key in DESIGN_KEYS if key in doc]
    require(len(present) <= 1, f'use only one of {", ".join(DESIGN_KEYS)} for replicate designs')
    return doc[present[0]] if present else None


def _district(item, plots, design_id, contrast_id):
    district = item.get('district')
    if district is not None:
        return district
    covers = item.get('covers', item.get('pair'))
    require(isinstance(covers, list) and len(covers) == 2,
            f'contrast needs a district or two covers: {design_id}/{contrast_id}')
    matching = sorted({row[0] for row in plots.values()
                       if set(covers) <= {cover for cover in row[3]}})
    require(len(matching) == 1,
            f'contrast district is ambiguous, declare it: {design_id}/{contrast_id}')
    return matching[0]


def load(rows, plots):
    """Validate declared replicate designs and return them as exact fractions."""
    require(isinstance(rows, list), 'replicate designs must be a list')
    designs, seen = [], set()
    districts = {row[0] for row in plots.values()}
    for row in rows:
        require(isinstance(row, dict), 'replicate design must be an object')
        design_id = _alias(row, ('id', 'design'), 'replicate design')
        require(isinstance(design_id, str) and design_id.strip() and design_id not in seen,
                f'invalid or duplicate replication id: {design_id!r}')
        seen.add(design_id)
        declared_groups = _alias(row, ('replicates', 'groups'), f'replicate design {design_id}')
        require(isinstance(declared_groups, list) and declared_groups,
                f'replicate design {design_id} needs at least one replicate group')
        replicates, replicate_ids = [], set()
        for rep in declared_groups:
            require(isinstance(rep, dict), f'replicate in {design_id} must be an object')
            rep_id = _alias(rep, ('id', 'group'), f'replicate in {design_id}')
            require(isinstance(rep_id, str) and rep_id.strip() and rep_id not in replicate_ids,
                    f'invalid or duplicate replicate id in {design_id}: {rep_id!r}')
            replicate_ids.add(rep_id)
            coefficient = number(
                _alias(rep, ('coefficient', 'variance_coefficient'), f'replicate {design_id}/{rep_id}'),
                f'replicate coefficient {design_id}/{rep_id}')
            require(coefficient > 0, f'replicate coefficient must be positive: {design_id}/{rep_id}')
            raw = _alias(rep, ('weights', 'expansion_ha'), f'replicate {design_id}/{rep_id}')
            replicates.append({'id': rep_id, 'coefficient': coefficient,
                               'weights': _weights(raw, plots, f'replicate {design_id}/{rep_id}')})
        contrasts, contrast_ids = [], set()
        items = row.get('contrasts', [])
        require(isinstance(items, list), f'contrasts of {design_id} must be a list')
        for item in items:
            require(isinstance(item, dict), f'contrast in {design_id} must be an object')
            contrast_id = item.get('id')
            require(isinstance(contrast_id, str) and contrast_id.strip()
                    and contrast_id not in contrast_ids,
                    f'invalid or duplicate contrast id in {design_id}: {contrast_id!r}')
            contrast_ids.add(contrast_id)
            covers = item.get('covers', item.get('pair'))
            require(isinstance(covers, list) and len(covers) == 2
                    and all(isinstance(c, str) and c.strip() for c in covers) and covers[0] != covers[1],
                    f'contrast covers must be two distinct cover ids: {design_id}/{contrast_id}')
            district = _district(item, plots, design_id, contrast_id)
            require(district in districts, f'unknown contrast district: {district!r}')
            known = {cover for owner, _, _, shares in plots.values() if owner == district
                     for cover, share in shares.items() if share > 0}
            require(set(covers) <= known, f'unknown cover in contrast: {design_id}/{contrast_id}')
            contrasts.append({'id': contrast_id, 'district': district, 'covers': list(covers)})
        designs.append({'id': design_id, 'replicates': replicates, 'contrasts': contrasts})
    return designs


def _empty_total():
    return {'visited_ha': Fraction(0), 'trees': Fraction(0), 'basal': Fraction(0)}


def _domains(facts):
    return sorted({(fact['district'], fact['cover']) for fact in facts})


def _totals(facts):
    """Visited area (denominator) and expanded trees/basal (numerators) per domain.

    The facts come from the same census expansion used for publication, so the
    numerator and denominator of a replicate are exactly the ones its own
    weights produce -- not a rescaling of the full-design ratio.
    """
    totals = {key: _empty_total() for key in _domains(facts)}
    for fact in facts:
        if fact['status'] != 'visited':
            continue
        total = totals[fact['district'], fact['cover']]
        total['visited_ha'] += Fraction(fact['represented_ha'])
        total['trees'] += Fraction(fact['expanded_trees'])
        total['basal'] += Fraction(fact['expanded_basal_pi_m2'])
    return totals


def _estimate(total):
    visited = total['visited_ha']
    return {'visited_ha': scalar(visited),
            'expanded_trees': scalar(total['trees']),
            'expanded_basal_pi_m2': scalar(total['basal']),
            'trees_per_visited_ha': scalar(total['trees'] / visited) if visited else None,
            'basal_pi_m2_per_visited_ha': scalar(total['basal'] / visited) if visited else None}


def _difference(first, second):
    return {metric: scalar(Fraction(first[metric]) - Fraction(second[metric])) for metric in METRICS}


def _variance(estimates, full, coefficients):
    values, errors = {}, {}
    for metric in METRICS:
        variance = sum(c * (Fraction(row[metric]) - Fraction(full[metric])) ** 2
                       for c, row in zip(coefficients, estimates))
        values[metric] = scalar(variance)
        errors[metric] = sqrt_text(variance)
    return values, errors


def _domain_report(key, full_total, replicates, totals_by_replicate):
    full = _estimate(full_total)
    rows, usable, unsupported = [], [], []
    for rep in replicates:
        total = totals_by_replicate[rep['id']][key]
        row = {'id': rep['id'], 'coefficient': scalar(rep['coefficient']),
               'supported': total['visited_ha'] > 0}
        row.update(_estimate(total))
        rows.append(row)
        (usable if row['supported'] else unsupported).append(rep)
    declared_sum = sum((rep['coefficient'] for rep in replicates), Fraction(0))
    coefficient_sum = sum((rep['coefficient'] for rep in usable), Fraction(0))
    if full[METRICS[0]] is None:
        uncertainty = 'unsupported'
    elif len(usable) < 2:
        uncertainty = 'insufficient_replicates'
    elif len(usable) < len(replicates):
        uncertainty = 'publishable_partial_replicates'
    else:
        uncertainty = 'publishable'
    variance = errors = None
    basis = None
    if len(usable) >= 2 and full[METRICS[0]] is not None:
        estimates = [_estimate(totals_by_replicate[rep['id']][key]) for rep in usable]
        variance, errors = _variance(estimates, full, [rep['coefficient'] for rep in usable])
        basis = ('all_declared_replicates' if len(usable) == len(replicates)
                 else 'usable_replicates_only')
    return {'district': key[0], 'cover': key[1],
            'status': 'publishable' if full[METRICS[0]] is not None else 'unsupported',
            'uncertainty_status': uncertainty,
            'full': full,
            'replicates': rows,
            'usable_replicates': [rep['id'] for rep in usable],
            'unsupported_replicates': [rep['id'] for rep in unsupported],
            'usable_coefficient_sum': scalar(coefficient_sum),
            'declared_coefficient_sum': scalar(declared_sum),
            'variance_basis': basis,
            'variance': variance,
            'standard_error': errors}


def _contrast_report(item, replicates, totals_by_replicate, full_totals):
    first_cover, second_cover = item['covers']
    first_key, second_key = (item['district'], first_cover), (item['district'], second_cover)
    first_full, second_full = _estimate(full_totals[first_key]), _estimate(full_totals[second_key])
    supported = first_full[METRICS[0]] is not None and second_full[METRICS[0]] is not None
    full = _difference(first_full, second_full) if supported else {metric: None for metric in METRICS}

    common, only_first, only_second, neither, differences = [], [], [], [], []
    for rep in replicates:
        a = _estimate(totals_by_replicate[rep['id']][first_key])
        b = _estimate(totals_by_replicate[rep['id']][second_key])
        a_ok, b_ok = a[METRICS[0]] is not None, b[METRICS[0]] is not None
        if a_ok and b_ok:
            common.append(rep['id'])
            differences.append({'id': rep['id'], 'coefficient': scalar(rep['coefficient']),
                                **_difference(a, b)})
        elif a_ok:
            # Intentionally unpaired: a one-cover group is never matched with a
            # group that supports only the other cover.
            only_first.append(rep['id'])
        elif b_ok:
            only_second.append(rep['id'])
        else:
            neither.append(rep['id'])

    declared_ids = [rep['id'] for rep in replicates]
    coefficients = {rep['id']: rep['coefficient'] for rep in replicates}
    coefficient_sum = sum((coefficients[rep_id] for rep_id in common), Fraction(0))
    if not supported:
        status = 'unsupported_full_sample'
    elif len(common) < 2:
        status = 'insufficient_common_replicates'
    elif common != declared_ids:
        # A contrast on a partial group set is not the declared design's
        # contrast; withholding it keeps the published variance honest.
        status = 'incomplete_common_replicates'
    else:
        status = 'publishable'

    variance = errors = None
    if status == 'publishable':
        variance, errors = _variance(differences, full, [coefficients[rep_id] for rep_id in common])
    return {'id': item['id'], 'district': item['district'], 'covers': item['covers'],
            'status': status,
            'full': {'supported': supported, **full},
            'declared_replicate_count': len(declared_ids),
            'common_replicate_count': len(common),
            'common_replicates': common,
            'common_coefficient_sum': scalar(coefficient_sum),
            'unpaired_replicates': {'only_first_cover': only_first,
                                    'only_second_cover': only_second,
                                    'neither_cover': neither},
            'replicate_differences': differences,
            'variance': variance,
            'standard_error': errors}


def _release(domains, contrasts):
    """Split what may be published from what must be withheld.

    A domain is publishable whenever its own full-sample numerator and
    denominator are supported, even if only some replicate groups support it.
    A contrast additionally needs every declared replicate group to supply both
    covers; otherwise the paired differences are incomplete and the contrast is
    reported but not published.
    """
    return {
        'publishable_domain_estimates': [
            {'district': d['district'], 'cover': d['cover'],
             'uncertainty_status': d['uncertainty_status']}
            for d in domains if d['status'] == 'publishable'],
        'unsupported_domains': [{'district': d['district'], 'cover': d['cover']}
                                for d in domains if d['status'] != 'publishable'],
        'domains_with_partial_uncertainty': [
            {'district': d['district'], 'cover': d['cover'],
             'usable_replicates': d['usable_replicates'],
             'unsupported_replicates': d['unsupported_replicates'],
             'usable_coefficient_sum': d['usable_coefficient_sum'],
             'declared_coefficient_sum': d['declared_coefficient_sum'],
             'variance_basis': d['variance_basis'],
             'variance': d['variance'],
             'standard_error': d['standard_error']}
            for d in domains if d['uncertainty_status'] == 'publishable_partial_replicates'],
        'domains_without_uncertainty': [
            {'district': d['district'], 'cover': d['cover'],
             'uncertainty_status': d['uncertainty_status']}
            for d in domains if d['uncertainty_status']
            in ('unsupported', 'insufficient_replicates')],
        'publishable_contrasts': [c['id'] for c in contrasts if c['status'] == 'publishable'],
        'withheld_contrasts': [{'id': c['id'], 'status': c['status'],
                                'common_replicate_count': c['common_replicate_count'],
                                'declared_replicate_count': c['declared_replicate_count']}
                               for c in contrasts if c['status'] != 'publishable'],
    }


def analyze(rows, plots, observations):
    designs = load(rows, plots)
    full_totals = _totals(expand(observations, default_weights(plots)))
    results = []
    for design in designs:
        totals_by_replicate = {rep['id']: _totals(expand(observations, rep['weights']))
                               for rep in design['replicates']}
        domains = [_domain_report(key, total, design['replicates'], totals_by_replicate)
                   for key, total in sorted(full_totals.items())]
        contrasts = [_contrast_report(item, design['replicates'], totals_by_replicate, full_totals)
                     for item in design['contrasts']]
        results.append({'id': design['id'],
                        'coefficient_sum': scalar(sum((rep['coefficient'] for rep in design['replicates']),
                                                      Fraction(0))),
                        'replicates': [{'id': rep['id'], 'coefficient': scalar(rep['coefficient'])}
                                       for rep in design['replicates']],
                        'domains': domains,
                        'contrasts': contrasts,
                        'release': _release(domains, contrasts)})
    return results
