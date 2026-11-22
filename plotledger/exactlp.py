"""Exact rational linear programming via the two-phase simplex method.

Every operation uses fractions.Fraction, so results are exact and
reproducible across runs. Bland's rule is used for both entering and
leaving choices, which guarantees termination.
"""
from fractions import Fraction

from .common import require


class InfeasibleError(ValueError):
    """The constraint system admits no solution."""


class UnboundedError(ValueError):
    """The objective can decrease without bound."""


def minimize(cost, rows, width):
    """Minimize cost·x subject to rows and x >= 0.

    rows is an iterable of (coefficients, sense, rhs) with sense one of
    '<=', '=', '>='; every coefficient list must have length `width`.
    Returns (optimal value, solution list) as exact Fractions.
    Raises InfeasibleError or UnboundedError when there is no optimum.
    """
    cost = [Fraction(c) for c in cost]
    require(len(cost) == width, 'objective width mismatch')
    table = []        # constraint coefficient rows (no rhs column)
    rhs = []          # right-hand sides, kept nonnegative
    basis = []        # basic column per row
    artificial = []   # columns that are artificial variables
    total = width     # current number of columns

    def new_column():
        nonlocal total
        for row in table:
            row.append(Fraction(0))
        total += 1
        return total - 1

    for raw_coeffs, sense, raw_rhs in rows:
        require(sense in ('<=', '=', '>='), f'unknown constraint sense: {sense}')
        row = [Fraction(c) for c in raw_coeffs]
        require(len(row) == width, 'constraint width mismatch')
        b = Fraction(raw_rhs)
        if b < 0:
            row = [-c for c in row]
            b = -b
            sense = {'<=': '>=', '>=': '<=', '=': '='}[sense]
        row.extend([Fraction(0)] * (total - width))
        if sense == '<=':
            col = new_column()
            row.append(Fraction(1))
        elif sense == '>=':
            new_column()
            row.append(Fraction(-1))
            col = new_column()
            row.append(Fraction(1))
            artificial.append(col)
        else:
            col = new_column()
            row.append(Fraction(1))
            artificial.append(col)
        table.append(row)
        rhs.append(b)
        basis.append(col)

    def pivot(r, c):
        pr = table[r]
        pivot_value = pr[c]
        table[r] = [v / pivot_value for v in pr]
        rhs[r] /= pivot_value
        for i in range(len(table)):
            if i == r:
                continue
            factor = table[i][c]
            if factor:
                table[i] = [v - factor * p for v, p in zip(table[i], table[r])]
                rhs[i] -= factor * rhs[r]
        basis[r] = c

    def run(objective):
        while True:
            reduced = [objective[basis[i]] for i in range(len(table))]
            enter = -1
            for j in range(total):
                if objective[j] - sum(reduced[i] * table[i][j] for i in range(len(table))) < 0:
                    enter = j
                    break
            if enter < 0:
                return
            leave = -1
            best = None
            for i in range(len(table)):
                a = table[i][enter]
                if a > 0:
                    ratio = rhs[i] / a
                    if best is None or ratio < best or (ratio == best and basis[i] < basis[leave]):
                        best = ratio
                        leave = i
            if leave < 0:
                raise UnboundedError('objective is unbounded below')
            pivot(leave, enter)

    if artificial:
        markers = set(artificial)
        run([Fraction(1) if j in markers else Fraction(0) for j in range(total)])
        if sum((rhs[i] for i in range(len(table)) if basis[i] in markers), Fraction(0)) > 0:
            raise InfeasibleError('constraints cannot be satisfied')
        # Drive remaining artificial basics (all at value zero) out of the
        # basis, then drop their columns and any redundant rows.
        for i in range(len(table)):
            if basis[i] in markers:
                swap = next((j for j in range(total) if j not in markers and table[i][j] != 0), None)
                if swap is not None:
                    pivot(i, swap)
        keep = [i for i in range(len(table))
                if any(table[i][j] != 0 for j in range(total) if j not in markers)]
        remap = {}
        for j in range(total):
            if j not in markers:
                remap[j] = len(remap)
        table = [[table[i][j] for j in remap] for i in keep]
        rhs = [rhs[i] for i in keep]
        basis = [remap[basis[i]] for i in keep]
        total = len(remap)

    run(cost + [Fraction(0)] * (total - width))
    solution = [Fraction(0)] * width
    for i in range(len(table)):
        if basis[i] < width:
            solution[basis[i]] = rhs[i]
    return sum((cost[j] * solution[j] for j in range(width)), Fraction(0)), solution
