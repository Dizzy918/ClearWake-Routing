"""
FleetProfile model (issue #86).

A fleet profile groups a company's vessels and their preferred operating
defaults (optimisation mode, emission target, preferred routes) so that
downstream services can apply policy without re-reading every vessel.
"""

from __future__ import annotations

from datetime import datetime

import mongoengine as me
from src.core.time_utils import utc_now


class FleetProfile(me.Document):
    company_id = me.ObjectIdField(required=True)
    name = me.StringField(required=True)
    description = me.StringField()
    vessel_ids = me.ListField(me.ObjectIdField(), default=list)
    default_optimization_mode = me.StringField(
        choices=["fastest", "eco"], default="fastest"
    )
    preferred_route_ids = me.ListField(me.ObjectIdField(), default=list)
    emission_target_kg_co2_per_nm = me.FloatField()
    created_at = me.DateTimeField(default=utc_now)
    updated_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "fleet_profiles",
        "indexes": [
            "company_id",
            {"fields": ["company_id", "name"], "unique": True},
        ],
    }

    def touch(self) -> None:
        self.updated_at = utc_now()
