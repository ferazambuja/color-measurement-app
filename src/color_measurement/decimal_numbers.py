"""One ASCII-decimal to binary64 policy for file and instrument protocols."""

import math
import re
from decimal import Decimal, InvalidOperation

DECIMAL_RE = re.compile(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z")


def decimal_number(raw: str) -> float:
    """Preserve representable subnormals and signed zero; reject fabricated zero.

    This is the existing CGATS conversion policy shared with direct serial
    protocols. Callers own framing, byte limits, field counts and error domains.
    """
    if not isinstance(raw, str) or DECIMAL_RE.fullmatch(raw) is None:
        raise ValueError(f"Invalid real number: {raw!r}")
    try:
        number = float(raw)
        rounded_to_zero = number == 0 and not Decimal(raw).is_zero()
    except (ValueError, OverflowError, InvalidOperation):
        raise ValueError(f"Invalid real number: {raw!r}") from None
    if not math.isfinite(number) or rounded_to_zero:
        raise ValueError(f"Real number is not finite or underflows to zero: {raw!r}")
    return number


def decimal_integer(raw: str) -> int:
    """Validate integer semantics on the original token before any rounding.

    Keep the existing finite binary64 resource/domain boundary, but compare the
    original standard-library Decimal exactly. Identifiers and integer protocol
    fields must not accept a fractional token that happens to round to an integer.
    """
    if not isinstance(raw, str) or DECIMAL_RE.fullmatch(raw) is None:
        raise ValueError(f"Invalid real number: {raw!r}")
    try:
        exact = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"Invalid real number: {raw!r}") from None
    if exact != exact.to_integral_value():
        raise ValueError(f"Protocol integer token is fractional: {raw!r}")
    decimal_number(raw)
    return int(exact)
