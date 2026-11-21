from collections import defaultdict

from .common import number, require, unique


def load_plots(rows):
    plots = {}
    for key, row in sorted(unique(rows).items()):
        area = number(row['plot_m2'], 'plot area')
        weight = number(row['expansion_ha'], 'expansion area')
        require(area > 0 and weight > 0, f'nonpositive geometry or weight: {key}')
        shares = defaultdict(int)
        for segment in unique(row['segments']).values():
            shares[segment['cover']] += number(segment['fraction'], 'segment fraction')
        require(sum(shares.values()) == 1, f'fractions must add to one: {key}')
        plots[key] = (row['district'], area, weight, dict(shares))
    return plots
