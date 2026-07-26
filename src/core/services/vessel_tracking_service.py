"""Recording where vessels are, and telling the fleet view about it.

A position report does three things: it appends to the vessel's track, it
updates the denormalised fields the fleet map reads, and it pushes the new fix
to that company's connected dashboards. Nothing else in the system polls for
positions, so this is the only place that has to be right.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from bson import ObjectId

from src.core.events.dispatcher import dispatcher
from src.core.time_utils import utc_now
from src.models.vessel import Vessel
from src.models.vessel_position import VesselPosition

logger = logging.getLogger(__name__)

# A report further than this from the last one, in the time elapsed, is not a
# ship — it is a bad fix or a swapped identifier. Kept generous: the fastest
# commercial vessels do about 45 knots.
MAX_PLAUSIBLE_SPEED_KNOTS = 80.0

# Reports do not arrive on a tidy cadence — feeds batch them, replay them, and
# occasionally deliver two within the same second. Dividing a real distance by
# a near-zero interval implies a nonsense speed and would reject good data, so
# the interval used for the test is floored here. Half a minute is short
# enough that a genuinely impossible jump still fails.
MIN_REPORT_WINDOW_SECONDS = 30.0


class ImplausibleJump(ValueError):
    """Raised when a fix would require an impossible speed to be genuine."""


def _haversine_nm(lon1, lat1, lon2, lat2) -> float:
    from math import asin, cos, radians, sin, sqrt

    r_nm = 3440.065
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r_nm * asin(min(1.0, sqrt(a)))


class VesselTrackingService:
    def record_position(
        self,
        vessel: Vessel,
        longitude: float,
        latitude: float,
        heading_deg: Optional[float] = None,
        speed_knots: Optional[float] = None,
        source: str = "gps",
        recorded_at=None,
        reject_implausible: bool = True,
    ) -> VesselPosition:
        """Store a fix, update the vessel, and notify the company."""
        if not (-180.0 <= longitude <= 180.0):
            raise ValueError(f"longitude out of range: {longitude}")
        if not (-90.0 <= latitude <= 90.0):
            raise ValueError(f"latitude out of range: {latitude}")

        recorded_at = recorded_at or utc_now()

        if reject_implausible:
            self._check_plausible(vessel, longitude, latitude, recorded_at)

        fix = VesselPosition(
            vessel_id=vessel.id,
            company_id=vessel.company_id,
            position={"type": "Point", "coordinates": [longitude, latitude]},
            heading_deg=heading_deg,
            speed_knots=speed_knots,
            source=source,
            recorded_at=recorded_at,
        )
        fix.save()

        vessel.current_position = {"type": "Point", "coordinates": [longitude, latitude]}
        vessel.heading_deg = heading_deg
        vessel.speed_knots = speed_knots
        vessel.position_updated_at = recorded_at
        vessel.save()

        dispatcher.broadcast_to_company(
            str(vessel.company_id),
            {
                "event_type": "vessel_position",
                "payload": {
                    "vessel_id": str(vessel.id),
                    "name": vessel.name,
                    "vessel_type": vessel.vessel_type,
                    "status": vessel.current_status,
                    "lon": longitude,
                    "lat": latitude,
                    "heading_deg": heading_deg,
                    "speed_knots": speed_knots,
                    "recorded_at": recorded_at.isoformat(),
                },
            },
        )
        return fix

    def _check_plausible(self, vessel: Vessel, longitude, latitude, recorded_at) -> None:
        previous = vessel.current_position
        previous_at = vessel.position_updated_at
        if not previous or not previous_at:
            return

        prev_lon, prev_lat = previous["coordinates"]
        elapsed_s = (recorded_at - previous_at).total_seconds()
        if elapsed_s <= 0:
            return  # out-of-order or duplicate report; distance test is meaningless

        elapsed_h = max(elapsed_s, MIN_REPORT_WINDOW_SECONDS) / 3600.0
        distance_nm = _haversine_nm(prev_lon, prev_lat, longitude, latitude)
        implied_knots = distance_nm / elapsed_h
        if implied_knots > MAX_PLAUSIBLE_SPEED_KNOTS:
            raise ImplausibleJump(
                f"{vessel.name}: {distance_nm:.0f} NM in {elapsed_h*60:.0f} min "
                f"implies {implied_knots:.0f} knots"
            )

    def track_for(self, vessel_id: str, limit: int = 500) -> list[VesselPosition]:
        return list(
            VesselPosition.objects(vessel_id=ObjectId(vessel_id))
            .order_by("-recorded_at")
            .limit(limit)
        )

    def latest_for_company(self, company_id: str) -> list[dict]:
        """Current position of every vessel that has ever reported one."""
        vessels = Vessel.objects(company_id=ObjectId(company_id))
        out = []
        for v in vessels:
            if not v.current_position:
                continue
            lon, lat = v.current_position["coordinates"]
            out.append(
                {
                    "vessel_id": str(v.id),
                    "name": v.name,
                    "imo_number": v.imo_number,
                    "vessel_type": v.vessel_type,
                    "status": v.current_status,
                    "lon": lon,
                    "lat": lat,
                    "heading_deg": v.heading_deg,
                    "speed_knots": v.speed_knots,
                    "destination_port": v.destination_port,
                    "position_updated_at": (
                        v.position_updated_at.isoformat() if v.position_updated_at else None
                    ),
                }
            )
        return out

    def vessels_without_recent_fix(self, company_id: str, stale_after_minutes: int = 60) -> Iterable:
        """Vessels that have gone quiet — usually a transponder problem."""
        cutoff = utc_now().timestamp() - stale_after_minutes * 60
        for v in Vessel.objects(company_id=ObjectId(company_id)):
            if v.position_updated_at and v.position_updated_at.timestamp() < cutoff:
                yield v
