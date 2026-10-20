"""Explicit modeled remaining effort, independent of source catalog publication."""

from math import ceil

REMAINING_EFFORT_METHOD = 'MODELED_COMPETENCY_CREDIT_V1'


def remaining_learning_hours(base_hours: int, credit: float) -> int:
    """Round incomplete learning up to a whole hour; full credit needs no root activity."""
    return max(0, ceil(base_hours * (1 - credit)))
