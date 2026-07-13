"""Time helpers.

``datetime.utcnow()`` is deprecated since Python 3.12. MongoDB (and
mongoengine) store and return *naive* UTC datetimes, so the drop-in
replacement must stay naive to keep comparisons with stored values valid.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a naive ``datetime`` (like utcnow did)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
