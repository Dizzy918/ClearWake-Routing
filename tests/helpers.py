"""Shared test helpers for authenticated API calls.

``login_as`` creates (or reuses) a User document directly in the mock DB and
sets a Bearer token as the client's default Authorization header, so existing
tests keep working against the now-authenticated API without going through
the (slow, PBKDF2-hashing) register/login HTTP flow in every test.
"""

from bson import ObjectId

from src.core.security import issue_access_token
from src.models.user import User

# Matches the company_id constants already used in test payloads.
DEFAULT_TEST_COMPANY_ID = "000000000000000000000001"


def login_as(client, company_id: str = DEFAULT_TEST_COMPANY_ID, role: str = "admin") -> str:
    """Authenticate *client* as a user of *company_id* and return the token."""
    email = f"testuser+{company_id}@clearwake.test"
    user = User.objects(email=email).first()
    if user is None:
        user = User(
            company_id=ObjectId(company_id),
            email=email,
            password_hash="not-used-in-tests",
            full_name="Test User",
            role=role,
        ).save()

    token = issue_access_token(
        user_id=str(user.id),
        company_id=company_id,
        role=role,
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return token
