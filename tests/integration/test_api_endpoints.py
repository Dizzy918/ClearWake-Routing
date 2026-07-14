import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from src.main import app
from tests.helpers import login_as

@pytest.fixture
def client():

    with patch("src.main.init_db"), patch("src.main.close_db"):
        import mongoengine
        import mongomock

        mongoengine.disconnect_all()
        mongoengine.connect(
            "testdb",
            host="mongodb://localhost",
            mongo_client_class=mongomock.MongoClient,
            uuidRepresentation="standard",
        )
        with TestClient(app) as test_client:
            # Payloads in this file use company 000000000000000000000001.
            login_as(test_client, "000000000000000000000001")
            yield test_client
        mongoengine.disconnect_all()

def test_get_ports(client: TestClient):
    response = client.get("/api/v1/routes/ports")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "port_id" in data[0]
    assert "name" in data[0]
    assert "lat" in data[0]

def test_calculate_route_fastest(client: TestClient):
    payload = {
        "company_id": "000000000000000000000001",
        "start_node_id": "GENOA",
        "end_node_id": "MARSEILLE",
        "optimization_mode": "fastest"
    }
    response = client.post("/api/v1/routes/calculate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "total_distance_nm" in data
    assert "waypoints" in data
    assert len(data["waypoints"]) >= 2
    assert data["waypoints"][0]["name"] == "Genoa"

def test_calculate_route_invalid_port(client: TestClient):
    payload = {
        "company_id": "000000000000000000000001",
        "start_node_id": "FAKE_PORT",
        "end_node_id": "MARSEILLE",
        "optimization_mode": "fastest"
    }
    response = client.post("/api/v1/routes/calculate", json=payload)
    assert response.status_code == 404
    data = response.json()
    assert "Port 'FAKE_PORT' not found" in data["detail"]


def test_get_landmask_geojson(client: TestClient):
    response = client.get("/api/v1/routes/landmask")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("type") == "FeatureCollection"
    assert isinstance(payload.get("features"), list)
    assert len(payload["features"]) > 0
