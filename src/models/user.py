"""
User model (issue #89).

Users belong to a Company (tenant) and carry a role. Authentication is
password-based; the access token is a JWT issued by /api/v1/auth/login
and verified by the get_current_user dependency.
"""

from __future__ import annotations

from datetime import datetime

import mongoengine as me
from src.core.time_utils import utc_now


ROLES: tuple[str, ...] = ("admin", "operator", "viewer")


class User(me.Document):
    company_id = me.ObjectIdField(required=True)
    email = me.EmailField(required=True, unique=True)
    password_hash = me.StringField(required=True)
    full_name = me.StringField()
    role = me.StringField(choices=ROLES, default="viewer")
    is_active = me.BooleanField(default=True)
    created_at = me.DateTimeField(default=utc_now)
    last_login_at = me.DateTimeField()

    meta = {
        "collection": "users",
        "indexes": [
            "email",
            "company_id",
            {"fields": ["company_id", "role"]},
        ],
    }
