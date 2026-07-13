"""
Port scheduling models.

Three documents:

* ``Port``           — DB-backed port record (mirrors the in-graph ports).
* ``PortSchedule``   — operating hours + blackout windows per port/berth.
* ``DockReservation``— a vessel's berth slot at a port (start_at → end_at).

DockReservation supports overlap-conflict detection: see
:meth:`DockReservation.conflicts_with`.
"""

from __future__ import annotations

from datetime import datetime

import mongoengine as me
from src.core.time_utils import utc_now


class Port(me.Document):
    """A physical port. ``port_id`` matches the graph node id."""
    port_id = me.StringField(required=True, unique=True)
    name = me.StringField(required=True)
    country = me.StringField()
    latitude = me.FloatField(required=True)
    longitude = me.FloatField(required=True)
    berth_count = me.IntField(default=1, min_value=1)
    timezone = me.StringField(default="UTC")
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "ports",
        "indexes": ["port_id", "country"],
        # Tolerate legacy fields left by earlier seeding (aliases,
        # is_waypoint, max_draft_m, …) so existing DBs keep loading.
        "strict": False,
    }


class PortSchedule(me.Document):
    """
    Per-port operating window.

    ``opens_at`` / ``closes_at`` are stored as minutes-since-midnight in
    the port's local timezone (0–1439). Blackouts are concrete UTC
    datetime ranges (maintenance, holidays, weather closures).
    """
    port_id = me.StringField(required=True)
    opens_at_min = me.IntField(default=0, min_value=0, max_value=1439)
    closes_at_min = me.IntField(default=1439, min_value=0, max_value=1439)
    operates_weekends = me.BooleanField(default=True)
    blackouts = me.ListField(
        me.DictField()  # {"start": dt, "end": dt, "reason": str}
    )
    notes = me.StringField()
    updated_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "port_schedules",
        "indexes": ["port_id"],
        "strict": False,
    }

    def is_blackout_active(self, when: datetime) -> bool:
        """True if *when* is inside any blackout window."""
        for window in self.blackouts or []:
            start = window.get("start")
            end = window.get("end")
            if start is None or end is None:
                continue
            if start <= when <= end:
                return True
        return False


class DockReservation(me.Document):
    """
    A vessel's reserved slot at a berth.

    Overlap check is open-interval: two reservations conflict iff the
    [start_at, end_at) windows overlap AND share the same port + berth.
    """
    port_id = me.StringField(required=True)
    berth_number = me.IntField(default=1, min_value=1)
    vessel_id = me.ObjectIdField(required=True)
    company_id = me.ObjectIdField()
    start_at = me.DateTimeField(required=True)
    end_at = me.DateTimeField(required=True)
    purpose = me.StringField(default="loading")
    status = me.StringField(
        choices=["scheduled", "active", "completed", "cancelled"],
        default="scheduled",
    )
    notes = me.StringField()
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "dock_reservations",
        "indexes": [
            "port_id",
            "vessel_id",
            ("port_id", "berth_number", "start_at"),
        ],
        "strict": False,
    }

    def clean(self):
        if self.end_at is None or self.start_at is None:
            raise me.ValidationError("start_at and end_at are required")
        if self.end_at <= self.start_at:
            raise me.ValidationError("end_at must be strictly after start_at")

    def conflicts_with(self, other: "DockReservation") -> bool:
        if self.port_id != other.port_id:
            return False
        if (self.berth_number or 1) != (other.berth_number or 1):
            return False
        if other.status == "cancelled" or self.status == "cancelled":
            return False
        # open-interval overlap
        return self.start_at < other.end_at and other.start_at < self.end_at
