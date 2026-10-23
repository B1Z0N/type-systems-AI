from billing import money
from billing.clock import add_interval, epoch, format_date, to_millis, to_seconds
from billing.fx import RateTable


def test_divide_round_half_even():
    assert money.divide_round(5, 2) == 2
    assert money.divide_round(7, 2) == 4
    assert money.divide_round(-5, 2) == -2
    assert money.divide_round(10, 3) == 3


def test_basis_points():
    assert money.apply_basis_points(10_000, 2_000) == 2_000
    assert money.apply_basis_points(999, 1_250) == 125


def test_allocate_sums_exactly():
    parts = money.allocate(1000, [1, 1, 1])
    assert sum(parts) == 1000
    assert parts == [334, 333, 333]


def test_format_and_parse():
    assert money.format_amount(123456, "usd") == "$1,234.56"
    assert money.format_amount(-500, "eur") == "-€5.00"
    assert money.format_amount(1200, "jpy") == "¥1,200"
    assert money.format_amount(1234, "kwd") == "1.234 KWD"
    assert money.parse_amount("12.345", "usd") == 1234
    assert money.parse_amount("7", "jpy") == 7


def test_month_math_clamps_to_month_end():
    jan31 = epoch(2026, 1, 31)
    feb = add_interval(jan31, "month", 1, 31)
    assert format_date(feb) == "2026-02-28"
    assert format_date(add_interval(feb, "month", 1, 31)) == "2026-03-31"
    assert format_date(add_interval(jan31, "year", 1, 31)) == "2027-01-31"


def test_millis_round_trip():
    t = epoch(2026, 3, 1)
    assert to_millis(t) == t * 1000
    assert to_seconds(to_millis(t) + 999) == t


def test_fx_convert_and_inverse():
    fx = RateTable()
    fx.set_rate("usd", "eur", "0.5")
    assert fx.convert(1000, "usd", "eur") == 500
    assert fx.convert(500, "eur", "usd") == 1000
    fx.set_rate("usd", "jpy", "150")
    assert fx.convert(1000, "usd", "jpy") == 1500
