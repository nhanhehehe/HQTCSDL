from decimal import ROUND_HALF_UP, Decimal

from app.core.web import format_decimal_clean


def format_cents_signed(cents: int) -> str:
    if cents == 0:
        return "0"
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:02d}".rstrip("0")


def round_avg_half_up(sum_value: Decimal, n_valid: int) -> str:
    """Exact fraction sum / n_valid rounded half-up away from zero to at most 2 decimals."""
    sum_q4 = int((sum_value * 10000).to_integral_exact())
    denom = 10000 * n_valid
    sign = -1 if sum_q4 < 0 else 1
    q, r = divmod(abs(sum_q4) * 100, denom)
    if 2 * r >= denom:
        q += 1
    return format_cents_signed(sign * q)


def round_stat_half_up(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format_decimal_clean(quantized)
