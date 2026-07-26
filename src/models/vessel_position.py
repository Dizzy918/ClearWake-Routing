"""Vessel position reports — the breadcrumb trail behind each ship.

The latest fix is denormalised onto the Vessel document so drawing the fleet
is one query; this collection keeps the history for track playback and for
working out where a vessel actually was when something happened.

Reports are capped by a TTL index rather than kept forever: a fleet reporting
every 30 seconds produces a great deal of data whose value decays quickly.
"""

from __future__ import annotations

import mongoengine as me

from src.core.time_utils import utc_now

# Keep roughly a month of track history.
POSITION_TTL_SECONDS = 30 * 24 * 60 * 60

# Where the fix came from. Operators care: a manual entry is not an AIS fix.
POSITION_SOURCES: tuple[str, ...] = ("ais", "gps", "manual", "simulated")


class VesselPosition(me.Document):
    vessel_id = me.ObjectIdField(required=True)
    company_id = me.ObjectIdField(required=True)
    position = me.PointField(required=True)
    heading_deg = me.FloatField(min_value=0, max_value=360)
    speed_knots = me.FloatField(min_value=0)
    source = me.StringField(choices=POSITION_SOURCES, default="gps")
    recorded_at = me.DateTimeField(default=utc_now, required=True)

    meta = {
        "collection": "vessel_positions",
        "indexes": [
            # The fleet-history query: one vessel, newest first.
            {"fields": ["vessel_id", "-recorded_at"]},
            {"fields": ["company_id", "-recorded_at"]},
            {"fields": ["position"], "cls": False},
            # Ages the trail out on its own.
            {"fields": ["recorded_at"], "expireAfterSeconds": POSITION_TTL_SECONDS},
        ],
        "ordering": ["-recorded_at"],
    }

    @property
    def longitude(self) -> float:
        return self.position["coordinates"][0]

    @property
    def latitude(self) -> float:
        return self.position["coordinates"][1]

    def to_dict(self) -> dict:
        return {
            "vessel_id": str(self.vessel_id),
            "lon": self.longitude,
            "lat": self.latitude,
            "heading_deg": self.heading_deg,
            "speed_knots": self.speed_knots,
            "source": self.source,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }
