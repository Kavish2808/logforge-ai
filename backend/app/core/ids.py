"""Unique, time-sortable event ID generation.

Uses ULID (Universally Unique Lexicographically Sortable Identifier) so
event IDs are both globally unique and naturally ordered by creation time,
which is useful for pagination/querying in the events table.
"""
from ulid import ULID


def generate_event_id() -> str:
    """Generate a new unique event ID (26-character ULID string)."""
    return str(ULID())
