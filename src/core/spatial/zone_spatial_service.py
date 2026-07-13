from datetime import datetime

from src.models.zone import Zone
from src.core.time_utils import utc_now


def _zone_is_currently_active(zone: Zone, now: datetime) -> bool:
    """True if status==active AND now is inside any valid_from / valid_until window."""
    if getattr(zone, "status", None) != "active":
        return False
    valid_from = getattr(zone, "valid_from", None)
    valid_until = getattr(zone, "valid_until", None)
    if valid_from is not None and now < valid_from:
        return False
    if valid_until is not None and now > valid_until:
        return False
    return True


class ZoneSpatialService:
    def get_zones_intersecting_point(self, longitude: float, latitude: float) -> list[Zone]:
        now = utc_now()
        candidates = list(
            Zone.objects(
                geometry__geo_intersects={
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                status="active",
            )
        )
        return [z for z in candidates if _zone_is_currently_active(z, now)]

    def get_zones_intersecting_route(self, coordinates: list[list[float]]) -> list[Zone]:
        now = utc_now()
        candidates = list(
            Zone.objects(
                geometry__geo_intersects={
                    "type": "LineString",
                    "coordinates": coordinates,
                },
                status="active",
            )
        )
        return [z for z in candidates if _zone_is_currently_active(z, now)]

    def is_point_in_any_zone(self, longitude: float, latitude: float) -> bool:
        return self.get_zones_intersecting_point(longitude, latitude) != []

    def get_blocking_zones(self, coordinates: list[list[float]], vessel=None) -> list[Zone]:
        intersecting = self.get_zones_intersecting_route(coordinates)
        blocking = []
        for zone in intersecting:
            if getattr(zone, 'zone_type', None) != "canal":
                blocking.append(zone)
            else:
                if vessel is None:
                    continue
                c = getattr(zone, 'canal_constraints', None)
                if not c:
                    continue

                if c.allowed_vessel_types and vessel.vessel_type not in c.allowed_vessel_types:
                    blocking.append(zone)
                    continue
                if c.blocked_vessel_types and vessel.vessel_type in c.blocked_vessel_types:
                    blocking.append(zone)
                    continue

                if c.max_draft_m and vessel.max_draft_m and vessel.max_draft_m > c.max_draft_m:
                    blocking.append(zone)
                    continue
                if c.max_length_m and vessel.length_m and vessel.length_m > c.max_length_m:
                    blocking.append(zone)
                    continue
                if c.max_beam_m and vessel.beam_m and vessel.beam_m > c.max_beam_m:
                    blocking.append(zone)
                    continue
        return blocking

    def is_route_blocked(self, coordinates: list[list[float]], vessel=None) -> bool:
        return len(self.get_blocking_zones(coordinates, vessel)) > 0
