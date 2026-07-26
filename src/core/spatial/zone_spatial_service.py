from datetime import datetime

from src.models.zone import Zone
from src.core.time_utils import utc_now
from src.core.spatial.geometry import (
    bbox_of_geometry,
    bboxes_overlap,
    coarse_segment_bbox,
    densify_geodesic,
    polyline_bbox,
    polyline_intersects_geometry,
)


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


class PrefetchedZoneSpatialService(ZoneSpatialService):
    """A ZoneSpatialService that loads the active zones once and then answers
    intersection questions in memory.

    Same answers as the parent, without a database round-trip per question.
    Pathfinding asks that question once per explored edge — thousands of times
    for a single route — so the parent's per-call ``$geoIntersects`` dominated
    the runtime of every eco route.

    The snapshot is taken when the instance is created, which is the right
    granularity for one route calculation: a zone opening or closing midway
    through a search would otherwise make the result inconsistent with itself.
    Create a new instance to pick up changes.
    """

    def __init__(self):
        now = utc_now()
        self._zones = [
            z for z in Zone.objects(status="active") if _zone_is_currently_active(z, now)
        ]
        # Cheap reject box per zone, computed once.
        self._boxes = [bbox_of_geometry(self._geometry_of(z)) for z in self._zones]

    @staticmethod
    def _geometry_of(zone) -> dict:
        geometry = getattr(zone, "geometry", None)
        if geometry is None:
            return {}
        # mongoengine returns a dict-like for GeoJSON fields; normalise it.
        if isinstance(geometry, dict):
            return geometry
        return {
            "type": getattr(geometry, "type", None),
            "coordinates": getattr(geometry, "coordinates", None),
        }

    @property
    def zone_count(self) -> int:
        return len(self._zones)

    def get_zones_intersecting_route(self, coordinates: list[list[float]]) -> list[Zone]:
        if not coordinates or len(coordinates) < 2:
            return []
        if not self._zones:
            return []

        legs = list(zip(coordinates, coordinates[1:]))
        # Cheap first: a padded endpoint box per leg, no trigonometry per point.
        coarse = [coarse_segment_bbox(a, b) for a, b in legs]
        # Densifying is the expensive step, so only do it for legs that survive
        # the coarse test against at least one zone — and only once each.
        arcs: dict[int, list] = {}

        hits = []
        for zone, box in zip(self._zones, self._boxes):
            geometry = self._geometry_of(zone)
            for i, (a, b) in enumerate(legs):
                if not bboxes_overlap(coarse[i], box):
                    continue
                arc = arcs.get(i)
                if arc is None:
                    arc = arcs[i] = densify_geodesic(a, b)
                if not bboxes_overlap(polyline_bbox(arc), box):
                    continue
                if polyline_intersects_geometry(arc, geometry):
                    hits.append(zone)
                    break
        return hits

    def get_zones_intersecting_point(self, longitude: float, latitude: float) -> list[Zone]:
        # A degenerate segment is exactly a point test.
        return self.get_zones_intersecting_route(
            [[longitude, latitude], [longitude, latitude]]
        )
