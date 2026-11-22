from collections import defaultdict

from .common import number, require, unique


def _zone_shares(row, key):
    raw = row.get('zone_shares')
    require(isinstance(raw, dict) and raw, f'missing zone shares: {key}')
    shares = {}
    for zone, value in raw.items():
        require(isinstance(zone, str) and zone.strip(), f'invalid zone name: {key}')
        shares[zone] = number(value, 'zone share')
    require(sum(shares.values()) == 1, f'zone shares must add to one: {key}')
    return shares


def _factor_bounds(row, key):
    raw = row.get('factor_bounds')
    require(isinstance(raw, list) and len(raw) == 2, f'invalid factor bounds: {key}')
    lo, hi = (number(x, 'factor bound') for x in raw)
    require(lo > 0, f'factor lower bound must be positive: {key}')
    require(lo <= hi, f'factor bounds are inverted: {key}')
    return lo, hi


def load_plots(rows, calibration=False):
    plots = {}
    for key, row in sorted(unique(rows).items()):
        area = number(row['plot_m2'], 'plot area')
        weight = number(row['expansion_ha'], 'expansion area')
        require(area > 0 and weight > 0, f'nonpositive geometry or weight: {key}')
        shares = defaultdict(int)
        for segment in unique(row['segments']).values():
            shares[segment['cover']] += number(segment['fraction'], 'segment fraction')
        require(sum(shares.values()) == 1, f'fractions must add to one: {key}')
        record = {'district': row['district'], 'area_m2': area, 'weight_ha': weight,
                  'shares': dict(shares)}
        if calibration:
            record['zone_shares'] = _zone_shares(row, key)
            record['factor_bounds'] = _factor_bounds(row, key)
        plots[key] = record
    return plots
