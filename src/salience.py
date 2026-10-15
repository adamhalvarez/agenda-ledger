"""Salience scoring. Deterministic, same shape as the ratio record-scanner
already runs in production (record activity over measured attention), with
the same rule: a floor on the denominator, never on the numerator, so a
zero-coverage item still ranks correctly high rather than being clipped.

record_activity is 1 per agenda item for this first version -- the
simplest defensible reading of "activity on the record" given the build
spec names no formula, only "record activity divided by coverage count,
with a defined floor." That makes ranking by ratio equivalent to ranking by
lowest coverage_count, which is transparent and easy to audit. A richer
record_activity (weighted by dollar_amount, or by how many separate
documents reference the item) is a reasonable future refinement, not
required by Phase 3's definition of done.
"""

DEFAULT_COVERAGE_FLOOR = 1


def compute_salience(record_activity: float, coverage_count: int, floor: int = DEFAULT_COVERAGE_FLOOR):
    if coverage_count < 0:
        raise ValueError("coverage_count cannot be negative")
    denominator = max(coverage_count, floor)
    return record_activity / denominator
