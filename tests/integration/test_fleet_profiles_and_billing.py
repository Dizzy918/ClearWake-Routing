"""
Integration tests for /api/v1/fleet-profiles and /api/v1/billing-data (#86).
"""

import pytest
import mongomock
import mongoengine
from bson import ObjectId
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


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
    from src.models.billing_data import BillingData
    from src.models.fleet_profile import FleetProfile

    FleetProfile.drop_collection()
    BillingData.drop_collection()


@pytest.fixture
def company_id(client) -> str:
    """A fresh tenant per test; the client authenticates as one of its users."""
    from tests.helpers import login_as

    cid = str(ObjectId())
    login_as(client, cid)
    return cid


class TestFleetProfiles:
    def test_create_then_list(self, client, company_id):
        payload = {
            "company_id": company_id,
            "name": "North Atlantic Tankers",
            "default_optimization_mode": "eco",
        }
        created = client.post("/api/v1/fleet-profiles/", json=payload)
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "North Atlantic Tankers"

        listed = client.get("/api/v1/fleet-profiles/", params={"company_id": company_id})
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    def test_invalid_optimization_mode_rejected(self, client, company_id):
        payload = {
            "company_id": company_id,
            "name": "X",
            "default_optimization_mode": "rocket",
        }
        resp = client.post("/api/v1/fleet-profiles/", json=payload)
        assert resp.status_code == 422

    def test_tenant_isolation_on_get(self, client, company_id):
        from src.infrastructure.repositories.fleet_profile_repository import (
            FleetProfileRepository,
        )

        other_company_id = str(ObjectId())
        client.post("/api/v1/fleet-profiles/", json={
            "company_id": company_id, "name": "A",
        })
        # The API refuses to write into another tenant, so seed the other
        # company's profile directly through the repository.
        other_profile = FleetProfileRepository().create({
            "company_id": ObjectId(other_company_id), "name": "B",
        })
        other_profile_id = str(other_profile.id)

        # Wrong tenant must NOT see the other tenant's profile.
        cross = client.get(
            f"/api/v1/fleet-profiles/{other_profile_id}",
            params={"company_id": company_id},
        )
        assert cross.status_code == 404

        # Asking with the other tenant's own id is forbidden outright.
        forbidden = client.get(
            f"/api/v1/fleet-profiles/{other_profile_id}",
            params={"company_id": other_company_id},
        )
        assert forbidden.status_code == 403

    def test_update_changes_fields(self, client, company_id):
        created = client.post("/api/v1/fleet-profiles/", json={
            "company_id": company_id, "name": "Original",
        }).json()
        pid = created["_id"]["$oid"]

        updated = client.patch(
            f"/api/v1/fleet-profiles/{pid}",
            params={"company_id": company_id},
            json={"name": "Renamed", "emission_target_kg_co2_per_nm": 2.5},
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "Renamed"
        assert body["emission_target_kg_co2_per_nm"] == 2.5

    def test_delete_removes_record(self, client, company_id):
        created = client.post("/api/v1/fleet-profiles/", json={
            "company_id": company_id, "name": "Disposable",
        }).json()
        pid = created["_id"]["$oid"]

        deleted = client.delete(
            f"/api/v1/fleet-profiles/{pid}",
            params={"company_id": company_id},
        )
        assert deleted.status_code == 200
        missing = client.get(
            f"/api/v1/fleet-profiles/{pid}",
            params={"company_id": company_id},
        )
        assert missing.status_code == 404


class TestBillingData:
    def test_create_then_get(self, client, company_id):
        payload = {
            "company_id": company_id,
            "billing_email": "billing@example.com",
            "subscription_tier": "starter",
        }
        created = client.post("/api/v1/billing-data/", json=payload)
        assert created.status_code == 200

        fetched = client.get("/api/v1/billing-data/", params={"company_id": company_id})
        assert fetched.status_code == 200
        assert fetched.json()["billing_email"] == "billing@example.com"
        assert fetched.json()["subscription_tier"] == "starter"

    def test_duplicate_create_returns_409(self, client, company_id):
        payload = {"company_id": company_id, "billing_email": "billing@example.com"}
        client.post("/api/v1/billing-data/", json=payload)
        again = client.post("/api/v1/billing-data/", json=payload)
        assert again.status_code == 409

    def test_get_returns_404_when_missing(self, client, company_id):
        resp = client.get("/api/v1/billing-data/", params={"company_id": company_id})
        assert resp.status_code == 404

    def test_append_usage_record(self, client, company_id):
        client.post("/api/v1/billing-data/", json={
            "company_id": company_id, "billing_email": "billing@example.com",
        })

        usage = {
            "period_start": "2026-06-01T00:00:00Z",
            "period_end": "2026-06-30T23:59:59Z",
            "route_calculations": 120,
            "ai_reroutes": 3,
            "api_calls": 4_500,
        }
        resp = client.post(
            "/api/v1/billing-data/usage",
            params={"company_id": company_id},
            json=usage,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["usage"]) == 1
        assert body["usage"][0]["route_calculations"] == 120

    def test_update_changes_subscription_tier(self, client, company_id):
        client.post("/api/v1/billing-data/", json={
            "company_id": company_id, "billing_email": "b@example.com",
        })
        resp = client.patch(
            "/api/v1/billing-data/",
            params={"company_id": company_id},
            json={"subscription_tier": "growth"},
        )
        assert resp.status_code == 200
        assert resp.json()["subscription_tier"] == "growth"
