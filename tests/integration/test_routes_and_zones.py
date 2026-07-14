"""
Integration tests for /api/v1/routes and /api/v1/zones endpoints.

Run with:  python -m pytest tests/integration/test_routes_and_zones.py -v
"""

import pytest
import mongomock
import mongoengine
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture(scope="session", autouse=True)
def mock_db():
    """
    Replace the real MongoDB connection with an in-memory mongomock instance
    for the entire test session. This means no real database is needed.
    """
    mongoengine.disconnect_all()
    mongoengine.connect(
        "testdb",
        host="mongodb://localhost",
        mongo_client_class=mongomock.MongoClient,
        uuidRepresentation="standard",
    )
    yield
    mongoengine.disconnect_all()


@pytest.fixture(scope="session")
def client(mock_db):
    from unittest.mock import MagicMock
    from src.core.events.dispatcher import dispatcher
    dispatcher._audit_repo = MagicMock()


    with patch("src.main.init_db"),\
         patch("src.main.close_db"):
        from src.main import app
        from tests.helpers import login_as
        with TestClient(app, raise_server_exceptions=True) as c:
            # Route payloads in this file use company 507f1f77bcf86cd799439011.
            login_as(c, "507f1f77bcf86cd799439011")
            yield c


@pytest.fixture(autouse=True)
def clean_collections():
    """Drop all documents between tests to ensure isolation."""
    from src.models.zone import Zone
    from src.models.route import Route
    Zone.drop_collection()
    Route.drop_collection()



VALID_ZONE_PAYLOAD = {
    "name": "Test Eco Zone",
    "zone_type": "eco",
    "status": "active",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [10.0, 30.0],
            [20.0, 30.0],
            [20.0, 40.0],
            [10.0, 40.0],
            [10.0, 30.0],
        ]]
    }
}

VALID_ROUTE_PAYLOAD = {
    "company_id": "507f1f77bcf86cd799439011",
    "vessel_id": "507f1f77bcf86cd799439012",
    "start_node_id": "MALTA",
    "end_node_id": "PIRAEUS",
    "optimization_mode": "fastest",
}



class TestGetZones:
    def test_get_all_zones_empty(self, client):
        response = client.get("/api/v1/zones/")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_all_zones_returns_created_zone(self, client):
        client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        response = client.get("/api/v1/zones/")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["name"] == "Test Eco Zone"

    def test_get_zones_filter_by_active_status(self, client):
        client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        inactive = {**VALID_ZONE_PAYLOAD, "name": "Inactive Zone", "status": "inactive"}
        client.post("/api/v1/zones/", json=inactive)

        response = client.get("/api/v1/zones/?status=active")
        assert response.status_code == 200
        data = response.json()
        assert all(z["status"] == "active" for z in data)

    def test_get_zones_filter_by_type(self, client):
        client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        conflict = {**VALID_ZONE_PAYLOAD, "name": "Conflict Zone", "zone_type": "conflict"}
        client.post("/api/v1/zones/", json=conflict)

        response = client.get("/api/v1/zones/?zone_type=eco")
        assert response.status_code == 200
        data = response.json()
        assert all(z["zone_type"] == "eco" for z in data)


class TestGetZoneById:
    def test_get_existing_zone(self, client):
        created = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD).json()
        zone_id = created["_id"]["$oid"]

        response = client.get(f"/api/v1/zones/{zone_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Test Eco Zone"

    def test_get_nonexistent_zone_returns_404(self, client):
        response = client.get("/api/v1/zones/507f1f77bcf86cd799439099")
        assert response.status_code == 404


class TestCreateZone:
    def test_create_zone_returns_201_or_200(self, client):
        response = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        assert response.status_code in (200, 201)

    def test_create_zone_persists_data(self, client):
        client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        zones = client.get("/api/v1/zones/").json()
        assert len(zones) == 1
        assert zones[0]["zone_type"] == "eco"

    def test_create_zone_default_status_is_active(self, client):
        payload = {**VALID_ZONE_PAYLOAD}
        payload.pop("status", None)
        response = client.post("/api/v1/zones/", json=payload)
        assert response.json()["status"] == "active"

    def test_create_multiple_zones(self, client):
        client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD)
        client.post("/api/v1/zones/", json={**VALID_ZONE_PAYLOAD, "name": "Zone 2"})
        zones = client.get("/api/v1/zones/").json()
        assert len(zones) == 2


class TestUpdateZone:
    def test_patch_zone_name(self, client):
        created = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD).json()
        zone_id = created["_id"]["$oid"]

        response = client.patch(f"/api/v1/zones/{zone_id}", json={"name": "Updated Name"})
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    def test_patch_nonexistent_zone_returns_404(self, client):
        response = client.patch(
            "/api/v1/zones/507f1f77bcf86cd799439099",
            json={"name": "X"}
        )
        assert response.status_code == 404


class TestActivateDeactivateZone:
    def test_deactivate_zone(self, client):
        created = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD).json()
        zone_id = created["_id"]["$oid"]

        with patch("src.core.events.dispatcher.AuditLogRepository"):
            response = client.post(f"/api/v1/zones/{zone_id}/deactivate")
        assert response.status_code == 200
        assert response.json()["status"] == "inactive"

    def test_activate_zone(self, client):
        inactive = {**VALID_ZONE_PAYLOAD, "status": "inactive"}
        created = client.post("/api/v1/zones/", json=inactive).json()
        zone_id = created["_id"]["$oid"]

        with patch("src.core.events.dispatcher.AuditLogRepository"):
            response = client.post(f"/api/v1/zones/{zone_id}/activate")
        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_activate_nonexistent_zone_returns_404(self, client):
        response = client.post("/api/v1/zones/507f1f77bcf86cd799439099/activate")
        assert response.status_code == 404

    def test_deactivate_nonexistent_zone_returns_404(self, client):
        response = client.post("/api/v1/zones/507f1f77bcf86cd799439099/deactivate")
        assert response.status_code == 404


class TestDeleteZone:
    def test_delete_existing_zone(self, client):
        created = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD).json()
        zone_id = created["_id"]["$oid"]

        response = client.delete(f"/api/v1/zones/{zone_id}")
        assert response.status_code == 200
        assert response.json()["deleted"] is True

    def test_delete_removes_zone_from_db(self, client):
        created = client.post("/api/v1/zones/", json=VALID_ZONE_PAYLOAD).json()
        zone_id = created["_id"]["$oid"]

        client.delete(f"/api/v1/zones/{zone_id}")
        response = client.get(f"/api/v1/zones/{zone_id}")
        assert response.status_code == 404

    def test_delete_nonexistent_zone_returns_404(self, client):
        response = client.delete("/api/v1/zones/507f1f77bcf86cd799439099")
        assert response.status_code == 404




class TestCalculateRoute:
    def test_calculate_fastest_route_success(self, client):
        response = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        assert response.status_code == 200
        data = response.json()
        assert "waypoints" in data
        assert len(data["waypoints"]) >= 2

    def test_calculate_eco_route_success(self, client):
        payload = {**VALID_ROUTE_PAYLOAD, "optimization_mode": "eco"}
        with patch("src.core.spatial.zone_spatial_service.ZoneSpatialService.is_route_blocked", return_value=False):
            response = client.post("/api/v1/routes/calculate", json=payload)
        assert response.status_code == 200

    def test_calculate_route_invalid_mode_returns_400(self, client):
        payload = {**VALID_ROUTE_PAYLOAD, "optimization_mode": "invalid"}
        response = client.post("/api/v1/routes/calculate", json=payload)
        assert response.status_code == 400

    def test_calculate_route_unknown_node_returns_404(self, client):
        payload = {**VALID_ROUTE_PAYLOAD, "start_node_id": "UNKNOWN"}
        response = client.post("/api/v1/routes/calculate", json=payload)
        assert response.status_code == 404

    def test_calculate_route_persists_to_db(self, client):
        response = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        assert response.status_code == 200
        route_id = response.json()["_id"]["$oid"]

        get_response = client.get(f"/api/v1/routes/{route_id}")
        assert get_response.status_code == 200

    def test_calculate_route_waypoints_have_correct_structure(self, client):
        response = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        assert response.status_code == 200
        waypoints = response.json()["waypoints"]
        for wp in waypoints:
            assert "sequence" in wp
            assert "coordinates" in wp
            assert len(wp["coordinates"]) == 2


class TestGetRouteById:
    def test_get_existing_route(self, client):
        created = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD).json()
        route_id = created["_id"]["$oid"]

        response = client.get(f"/api/v1/routes/{route_id}")
        assert response.status_code == 200

    def test_get_nonexistent_route_returns_404(self, client):
        response = client.get("/api/v1/routes/507f1f77bcf86cd799439099")
        assert response.status_code == 404


class TestCalculateBatchRoutes:
    def test_batch_calculate_returns_list(self, client):
        payload = [VALID_ROUTE_PAYLOAD, VALID_ROUTE_PAYLOAD]
        response = client.post("/api/v1/routes/calculate-batch", json=payload)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) == 2

    def test_batch_calculate_all_have_waypoints(self, client):
        payload = [VALID_ROUTE_PAYLOAD, VALID_ROUTE_PAYLOAD]
        response = client.post("/api/v1/routes/calculate-batch", json=payload)
        results = response.json()
        assert all("waypoints" in r for r in results)

    def test_batch_calculate_empty_list(self, client):
        response = client.post("/api/v1/routes/calculate-batch", json=[])
        assert response.status_code == 200
        assert response.json() == []

    def test_batch_with_invalid_mode_returns_error_in_result(self, client):
        payload = [
            VALID_ROUTE_PAYLOAD,
            {**VALID_ROUTE_PAYLOAD, "optimization_mode": "invalid"},
        ]
        response = client.post("/api/v1/routes/calculate-batch", json=payload)
        assert response.status_code == 200
        results = response.json()
        assert "waypoints" in results[0]
        assert "error" in results[1]


class TestRouteCache:
    def test_repeat_request_is_cache_hit(self, client):
        from src.api.v1.routers.routes import _ROUTE_CACHE
        _ROUTE_CACHE.clear()

        first = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        assert first.status_code == 200
        assert first.json()["cache_hit"] is False

        second = client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        assert second.status_code == 200
        assert second.json()["cache_hit"] is True

        assert first.json()["waypoints"] == second.json()["waypoints"]
        assert first.json()["total_distance_nm"] == second.json()["total_distance_nm"]

    def test_different_mode_is_not_cache_hit(self, client):
        from src.api.v1.routers.routes import _ROUTE_CACHE
        _ROUTE_CACHE.clear()

        client.post("/api/v1/routes/calculate", json=VALID_ROUTE_PAYLOAD)
        with patch("src.core.spatial.zone_spatial_service.ZoneSpatialService.is_route_blocked", return_value=False):
            other = client.post(
                "/api/v1/routes/calculate",
                json={**VALID_ROUTE_PAYLOAD, "optimization_mode": "eco"},
            )
        assert other.status_code == 200
        assert other.json()["cache_hit"] is False

    def test_cache_evicts_oldest_when_full(self, client):
        from src.api.v1.routers.routes import (
            _ROUTE_CACHE,
            _ROUTE_CACHE_MAX,
            _route_cache_set,
        )
        _ROUTE_CACHE.clear()
        for i in range(_ROUTE_CACHE_MAX + 5):
            _route_cache_set(
                (f"START_{i}", "END", "", "", "fastest"),
                {"waypoints": [], "stats": {}},
            )
        assert len(_ROUTE_CACHE) == _ROUTE_CACHE_MAX
        # The first 5 keys should have been evicted.
        assert ("START_0", "END", "", "", "fastest") not in _ROUTE_CACHE
        assert ("START_4", "END", "", "", "fastest") not in _ROUTE_CACHE
        assert ("START_5", "END", "", "", "fastest") in _ROUTE_CACHE
