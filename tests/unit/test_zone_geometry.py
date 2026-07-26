"""The in-memory zone test must never be more permissive than the database.

PrefetchedZoneSpatialService answers zone-intersection questions locally so
pathfinding does not need a $geoIntersects round-trip per explored edge. That
is only safe if it agrees with MongoDB — and where it cannot agree exactly, it
must fail towards blocking, never towards allowing a route through a protected
zone.

These tests pin both properties down.
"""

import pytest

from src.core.spatial.geometry import (
    densify_geodesic,
    great_circle_km,
    point_in_polygon,
    polyline_intersects_geometry,
    segments_intersect,
)
from src.core.spatial.zone_spatial_service import (
    PrefetchedZoneSpatialService,
    ZoneSpatialService,
)

SQUARE = [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]


class TestPrimitives:
    def test_segments_crossing(self):
        assert segments_intersect([0, 0], [2, 2], [0, 2], [2, 0])

    def test_segments_apart(self):
        assert not segments_intersect([0, 0], [1, 0], [0, 1], [1, 1])

    def test_touching_endpoint_counts(self):
        assert segments_intersect([0, 0], [1, 1], [1, 1], [2, 0])

    def test_point_inside(self):
        assert point_in_polygon([1.0, 1.0], SQUARE)

    def test_point_outside(self):
        assert not point_in_polygon([3.0, 1.0], SQUARE)

    def test_point_on_boundary_counts_as_inside(self):
        # Deliberately conservative: grazing a protected zone is treated as
        # entering it.
        assert point_in_polygon([0.0, 1.0], SQUARE)

    def test_hole_is_excluded(self):
        with_hole = SQUARE + [[[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]]
        assert not point_in_polygon([1.0, 1.0], with_hole)
        assert point_in_polygon([0.2, 0.2], with_hole)


class TestGeodesic:
    def test_short_hop_is_not_subdivided(self):
        assert len(densify_geodesic([0.0, 0.0], [0.05, 0.0])) == 2

    def test_long_hop_is_subdivided(self):
        arc = densify_geodesic([-9.14, 38.72], [-5.0, 35.0])
        assert len(arc) > 10

    def test_arc_endpoints_are_preserved(self):
        a, b = [-9.14, 38.72], [-5.0, 35.0]
        arc = densify_geodesic(a, b)
        assert arc[0] == pytest.approx(a, abs=1e-9)
        assert arc[-1] == pytest.approx(b, abs=1e-9)

    def test_arc_bows_off_the_straight_lonlat_line(self):
        # The reason the planar test alone disagreed with the database.
        a, b = [0.0, 60.0], [60.0, 60.0]
        arc = densify_geodesic(a, b)
        assert max(p[1] for p in arc) > 60.5

    def test_distance_is_sane(self):
        assert great_circle_km([0, 0], [0, 1]) == pytest.approx(111.19, abs=0.5)

    def test_geometry_types_it_cannot_index_are_not_blocking(self):
        assert not polyline_intersects_geometry(
            [[0, 0], [1, 1]], {"type": "Point", "coordinates": [0, 0]}
        )


@pytest.fixture(scope="module")
def real_mongo():
    """A real MongoDB, or skip.

    This comparison cannot run on mongomock: it has no $geoIntersects, which is
    precisely the operator under test. It runs wherever a real mongod is
    reachable and skips politely everywhere else.
    """
    pymongo = pytest.importorskip("pymongo")
    from src.core.config import settings

    try:
        client = pymongo.MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no reachable MongoDB for the geo comparison: {exc}")

    import mongoengine

    mongoengine.disconnect_all()
    from src.infrastructure.database.database import init_db

    init_db()
    yield
    mongoengine.disconnect_all()


@pytest.mark.usefixtures("real_mongo")
class TestAgreesWithMongo:
    """Compare the local implementation against the database it replaces."""

    def _edges(self):
        from src.core.graph_builder import build_navigation_graph

        graph = build_navigation_graph()
        return [
            (e.source.longitude, e.source.latitude, e.destination.longitude, e.destination.latitude)
            for node in getattr(graph, "_nodes", {})
            for e in graph.get_edges(node)
        ]

    def test_never_more_permissive_than_the_database(self):
        db = ZoneSpatialService()
        memory = PrefetchedZoneSpatialService()

        unsafe = []
        for lon1, lat1, lon2, lat2 in self._edges():
            segment = [[lon1, lat1], [lon2, lat2]]
            if bool(db.get_zones_intersecting_route(segment)) and not bool(
                memory.get_zones_intersecting_route(segment)
            ):
                unsafe.append(segment)

        assert unsafe == [], (
            f"{len(unsafe)} edges would be routed through a zone the database blocks; "
            "the in-memory test may only ever be stricter."
        )

    def test_agreement_is_near_total(self):
        # Divergence is confined to edges that graze a zone boundary exactly
        # or cross the antimeridian. If this ratio moves, something else broke.
        db = ZoneSpatialService()
        memory = PrefetchedZoneSpatialService()

        edges = self._edges()
        differing = sum(
            bool(db.get_zones_intersecting_route([[a, b], [c, d]]))
            != bool(memory.get_zones_intersecting_route([[a, b], [c, d]]))
            for a, b, c, d in edges
        )
        assert differing / len(edges) < 0.01
