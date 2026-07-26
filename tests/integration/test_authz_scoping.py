"""
Integration tests for API-wide authentication and company scoping.

Every business router now sits behind get_current_user, and endpoints that
take a company_id verify it against the caller's `cid` JWT claim.
"""

import pytest
import mongomock
import mongoengine
from bson import ObjectId
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from tests.helpers import login_as


@pytest.fixture(scope="session", autouse=True)
def mock_db():
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
    from src.core.events.dispatcher import dispatcher
    dispatcher._audit_repo = MagicMock()

    with patch("src.main.init_db"), patch("src.main.close_db"):
        from src.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture(autouse=True)
def clean_collections():
    from src.models.user import User
    from src.models.vessel import Vessel

    User.drop_collection()
    Vessel.drop_collection()


@pytest.fixture
def company_id() -> str:
    return str(ObjectId())


PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/vessels/"),
    ("GET", "/api/v1/companies/"),
    ("GET", "/api/v1/billing-data/?company_id=000000000000000000000001"),
    ("GET", "/api/v1/fleet-profiles/?company_id=000000000000000000000001"),
    ("GET", "/api/v1/zones/"),
    ("GET", "/api/v1/routes/ports"),
    ("GET", "/api/v1/routes/history"),
    ("GET", "/api/v1/weather/regions"),
    ("GET", "/api/v1/analytics/strategy-effectiveness"),
    ("GET", "/api/v1/ai/recommendations"),
    ("GET", "/api/v1/ports"),
    ("POST", "/api/v1/routes/calculate"),
    ("POST", "/api/v1/optimization/draft-trim"),
    ("POST", "/api/v1/routing/calculate-parallel"),
]


# Reachable without a token, deliberately. The map is served before anyone can
# log in and cannot draw a coastline without this. When it was accidentally
# swept behind auth, the browser silently fell back to pulling a 591 kB GeoJSON
# from raw.githubusercontent.com on every single load.
PUBLIC_ENDPOINTS = [
    ("GET", "/api/v1/routes/landmask"),
]

# Changing a zone reroutes ships — including, for shared zones, other
# operators' ships. A read-only account must never be able to do it.
VIEWER_FORBIDDEN_ENDPOINTS = [
    ("POST", "/api/v1/zones/"),
    ("POST", "/api/v1/zones/circle"),
    ("POST", "/api/v1/zones/presets/suez_canal"),
    ("POST", "/api/v1/zones/import/nga"),
    ("PATCH", "/api/v1/zones/000000000000000000000001"),
    ("DELETE", "/api/v1/zones/000000000000000000000001"),
    ("POST", "/api/v1/zones/000000000000000000000001/activate"),
    ("POST", "/api/v1/zones/000000000000000000000001/deactivate"),
    ("POST", "/api/v1/vessels/000000000000000000000001/course"),
    ("DELETE", "/api/v1/vessels/000000000000000000000001/course"),
    ("POST", "/api/v1/vessels/000000000000000000000001/position"),
]


class TestAuthenticationRequired:
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_unauthenticated_request_is_rejected(self, client, method, path):
        headers = {k: v for k, v in client.headers.items() if k != "authorization"}
        resp = client.request(method, path, headers=headers)
        # Requests without a bearer token never reach the handler.
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"

    @pytest.mark.parametrize("method,path", PUBLIC_ENDPOINTS)
    def test_public_reference_data_stays_reachable(self, client, method, path):
        headers = {k: v for k, v in client.headers.items() if k != "authorization"}
        resp = client.request(method, path, headers=headers)
        assert (
            resp.status_code != 401
        ), f"{method} {path} is behind auth again — the map cannot load it before login"

    def test_garbage_token_is_rejected(self, client):
        resp = client.get(
            "/api/v1/vessels/",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_auth_endpoints_stay_open(self, client):
        # login must be reachable without a token (it returns 401 for bad
        # credentials, not for a missing token).
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@x.com", "password": "nope"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"


class TestViewerCannotChangeTheWorld:
    """A read-only account must not be able to reroute anybody's ships.

    Zone changes are the sharp edge: a shared zone (an official closure)
    affects every operator's routing, so letting a viewer close one would let
    one customer disrupt another's fleet.
    """

    @pytest.mark.parametrize("method,path", VIEWER_FORBIDDEN_ENDPOINTS)
    def test_viewer_is_refused(self, client, method, path):
        login_as(client, str(ObjectId()), role="viewer")
        resp = client.request(method, path, json={})
        assert resp.status_code == 403, (
            f"viewer reached {method} {path} -> {resp.status_code}; "
            "a read-only account must not be able to change routing"
        )

    def test_viewer_can_still_read(self, client, company_id):
        login_as(client, company_id, role="viewer")
        assert client.get("/api/v1/zones/").status_code == 200


class TestCompanyScoping:
    def test_vessel_list_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        other = str(ObjectId())
        resp = client.get("/api/v1/vessels/", params={"company_id": other})
        assert resp.status_code == 403

    def test_vessel_create_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        other = str(ObjectId())
        resp = client.post("/api/v1/vessels/", json={
            "company_id": other,
            "name": "Intruder",
            "imo_number": "IMO9000001",
            "vessel_type": "tanker",
        })
        assert resp.status_code == 403

    def test_cross_tenant_vessel_read_is_hidden(self, client, company_id):
        login_as(client, company_id)
        created = client.post("/api/v1/vessels/", json={
            "company_id": company_id,
            "name": "Own Ship",
            "imo_number": "IMO9000002",
            "vessel_type": "tanker",
        })
        assert created.status_code == 200
        vessel_id = created.json()["_id"]["$oid"]

        # Same vessel, different tenant: must look like it doesn't exist.
        login_as(client, str(ObjectId()))
        resp = client.get(f"/api/v1/vessels/{vessel_id}")
        assert resp.status_code == 404

    def test_vessel_list_only_shows_own_company(self, client, company_id):
        login_as(client, company_id)
        client.post("/api/v1/vessels/", json={
            "company_id": company_id, "name": "Mine",
            "imo_number": "IMO9000003", "vessel_type": "tanker",
        })

        other = str(ObjectId())
        login_as(client, other)
        client.post("/api/v1/vessels/", json={
            "company_id": other, "name": "Theirs",
            "imo_number": "IMO9000004", "vessel_type": "tanker",
        })

        listed = client.get("/api/v1/vessels/").json()
        assert [v["name"] for v in listed] == ["Theirs"]

    def test_billing_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        resp = client.get(
            "/api/v1/billing-data/", params={"company_id": str(ObjectId())},
        )
        assert resp.status_code == 403

    def test_company_detail_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        resp = client.get(f"/api/v1/companies/{ObjectId()}")
        assert resp.status_code == 403

    def test_analytics_company_summary_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        resp = client.get(f"/api/v1/analytics/companies/{ObjectId()}")
        assert resp.status_code == 403

    def test_route_calculate_rejects_other_company(self, client, company_id):
        login_as(client, company_id)
        resp = client.post("/api/v1/routes/calculate", json={
            "company_id": str(ObjectId()),
            "start_node_id": "MALTA",
            "end_node_id": "PIRAEUS",
            "optimization_mode": "fastest",
        })
        assert resp.status_code == 403
