"""Planar geometry predicates for testing a route segment against a zone.

Why this exists
---------------
Zone avoidance used to ask MongoDB ``$geoIntersects`` once per edge that A*
explored — about 2 000 sequential round-trips for a single cross-graph route,
which was 99% of the wall-clock time of an eco route. There are only a handful
of zones, so the whole set fits in memory and every one of those questions can
be answered locally.

Accuracy
--------
MongoDB's 2dsphere index answers on the sphere; this module answers on the
plane. For the short edges in the navigation graph the two agree, and
``tests/unit/test_zone_geometry.py`` checks that against the database for a
large sample of real edges rather than taking it on trust.

Segments that cross the antimeridian are the known exception: in lon/lat space
they appear to span the globe. ``segment_intersects_geometry`` detects that
case and reports an intersection, which is the safe direction to be wrong in —
a route may be rejected that a sphere would allow, never the reverse.
"""

import math
from typing import Sequence

Point = Sequence[float]
Ring = Sequence[Point]

# A 2dsphere LineString is a great circle, which over a few hundred kilometres
# bows measurably away from the straight line between the same two points in
# lon/lat space. Splitting each edge into hops no longer than this keeps the
# planar approximation of that arc within a few hundred metres, which is far
# below the precision of the zone polygons.
_MAX_HOP_KM = 25.0
_EARTH_RADIUS_KM = 6371.0088


def _to_cartesian(lon: float, lat: float):
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    cos_lat = math.cos(lat_r)
    return (cos_lat * math.cos(lon_r), cos_lat * math.sin(lon_r), math.sin(lat_r))


def _to_lonlat(x: float, y: float, z: float):
    return (math.degrees(math.atan2(y, x)), math.degrees(math.asin(max(-1.0, min(1.0, z)))))


def great_circle_km(a: Point, b: Point) -> float:
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def densify_geodesic(a: Point, b: Point, max_hop_km: float = _MAX_HOP_KM):
    """Points along the great circle from a to b, spaced at most max_hop_km.

    Returned as a lon/lat polyline that a planar test can walk, so the caller
    approximates the same arc the database indexes.
    """
    distance = great_circle_km(a, b)
    steps = int(distance / max_hop_km) + 1
    if steps <= 1:
        return [a, b]

    ax, ay, az = _to_cartesian(a[0], a[1])
    bx, by, bz = _to_cartesian(b[0], b[1])
    dot = max(-1.0, min(1.0, ax * bx + ay * by + az * bz))
    omega = math.acos(dot)
    if omega < 1e-12:
        return [a, b]
    sin_omega = math.sin(omega)

    out = []
    for i in range(steps + 1):
        t = i / steps
        s1 = math.sin((1 - t) * omega) / sin_omega
        s2 = math.sin(t * omega) / sin_omega
        out.append(_to_lonlat(s1 * ax + s2 * bx, s1 * ay + s2 * by, s1 * az + s2 * bz))
    return out


def _orientation(a: Point, b: Point, c: Point) -> float:
    """Twice the signed area of triangle abc; sign gives the turn direction."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    """True if collinear point p lies within the bounding box of segment ab."""
    return (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """True if segment p1p2 touches or crosses segment p3p4."""
    d1 = _orientation(p3, p4, p1)
    d2 = _orientation(p3, p4, p2)
    d3 = _orientation(p1, p2, p3)
    d4 = _orientation(p1, p2, p4)

    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True

    # Collinear touching cases.
    if d1 == 0 and _on_segment(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment(p1, p2, p4):
        return True
    return False


def point_in_ring(point: Point, ring: Ring) -> bool:
    """Ray casting. Points exactly on the boundary count as inside."""
    x, y = point[0], point[1]
    inside = False
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i][0], ring[i][1]
        bx, by = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        # Boundary hit.
        if _orientation((ax, ay), (bx, by), (x, y)) == 0 and _on_segment(
            (ax, ay), (bx, by), (x, y)
        ):
            return True
        if (ay > y) != (by > y):
            x_cross = ax + (y - ay) / (by - ay) * (bx - ax)
            if x_cross > x:
                inside = not inside
    return inside


def point_in_polygon(point: Point, rings: Sequence[Ring]) -> bool:
    """GeoJSON polygon: first ring is the exterior, the rest are holes."""
    if not rings or not point_in_ring(point, rings[0]):
        return False
    return not any(point_in_ring(point, hole) for hole in rings[1:])


def _straight_segment_hits_rings(a: Point, b: Point, rings: Sequence[Ring]) -> bool:
    if not rings:
        return False
    # Either endpoint inside the polygon proper.
    if point_in_polygon(a, rings) or point_in_polygon(b, rings):
        return True
    # Or the segment crosses any ring, exterior or hole.
    for ring in rings:
        n = len(ring)
        for i in range(n):
            if segments_intersect(a, b, ring[i], ring[(i + 1) % n]):
                return True
    return False


def _polyline_hits_rings(points: Sequence[Point], rings: Sequence[Ring]) -> bool:
    return any(
        _straight_segment_hits_rings(p, q, rings) for p, q in zip(points, points[1:])
    )


def _crosses_antimeridian(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) > 180.0


def segment_intersects_geometry(a: Point, b: Point, geometry: dict) -> bool:
    """True if segment ab intersects a GeoJSON Polygon or MultiPolygon.

    Unknown geometry types return False — the caller treats that as "not
    blocking", matching what the database does with a type it cannot index.
    """
    if not geometry:
        return False

    if _crosses_antimeridian(a, b):
        # Cannot be represented as one planar segment; fail safe.
        return True

    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return False

    # Walk the great circle, not the straight lon/lat line, so this agrees
    # with what a 2dsphere index would answer.
    arc = densify_geodesic(a, b)

    if gtype == "Polygon":
        return _polyline_hits_rings(arc, coords)
    if gtype == "MultiPolygon":
        return any(_polyline_hits_rings(arc, poly) for poly in coords)
    return False


def bbox_of_geometry(geometry: dict):
    """(min_lon, min_lat, max_lon, max_lat), or None if it cannot be derived.

    Used to reject most segments with four comparisons before doing the real
    work.
    """
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None

    if gtype == "Polygon":
        rings = [coords[0]] if coords else []
    elif gtype == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    else:
        return None

    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def polyline_bbox(points: Sequence[Point]):
    """(min_lon, min_lat, max_lon, max_lat) of an arc.

    Taken over the densified arc rather than its two endpoints: a great circle
    bows poleward of the straight line, so an endpoint-only box can sit
    entirely inside a zone's box while the arc that matters does not — or the
    reverse, which would reject an edge that really does cross.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bboxes_overlap(a, b) -> bool:
    if a is None or b is None:
        return True
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def coarse_segment_bbox(a: Point, b: Point):
    """A box guaranteed to contain the great circle between a and b.

    Densifying an arc is the expensive part, so this is used to throw away
    edges nowhere near a zone before paying for it. The endpoint box is
    padded by the edge's own angular length, which comfortably exceeds how far
    the arc can bow away from it — deliberately generous, since being too
    large only costs a wasted check, while being too small would miss a
    crossing.
    """
    pad = great_circle_km(a, b) / 111.0
    return (
        min(a[0], b[0]) - pad,
        min(a[1], b[1]) - pad,
        max(a[0], b[0]) + pad,
        max(a[1], b[1]) + pad,
    )


def polyline_intersects_geometry(points: Sequence[Point], geometry: dict) -> bool:
    """As segment_intersects_geometry, for an already-densified arc."""
    if not geometry or len(points) < 2:
        return False
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return False
    if gtype == "Polygon":
        return _polyline_hits_rings(points, coords)
    if gtype == "MultiPolygon":
        return any(_polyline_hits_rings(points, poly) for poly in coords)
    return False
