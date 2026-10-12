"""Pure trading decisions shared by live execution and sample simulation."""

import math


def discount_is_acceptable(ratio: float, limit: float) -> bool:
    return math.isfinite(ratio) and math.isfinite(limit) and 0 < ratio < limit
