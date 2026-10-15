import pytest

from src.salience import compute_salience


def test_zero_coverage_ranks_highest_via_the_floor():
    zero_coverage = compute_salience(record_activity=1, coverage_count=0)
    some_coverage = compute_salience(record_activity=1, coverage_count=1)
    assert zero_coverage == some_coverage  # floor=1 makes 0 and 1 equivalent denominators
    assert zero_coverage == 1.0


def test_more_coverage_ranks_lower():
    low = compute_salience(record_activity=1, coverage_count=1)
    high = compute_salience(record_activity=1, coverage_count=10)
    assert high < low


def test_floor_is_configurable():
    result = compute_salience(record_activity=1, coverage_count=0, floor=5)
    assert result == 1 / 5


def test_negative_coverage_count_raises():
    with pytest.raises(ValueError):
        compute_salience(record_activity=1, coverage_count=-1)


def test_ratio_scales_with_record_activity():
    assert compute_salience(record_activity=4, coverage_count=2) == 2.0
