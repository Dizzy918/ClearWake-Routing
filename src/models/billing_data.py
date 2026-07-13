"""
BillingData model (issue #86).

Captures per-company billing details, subscription state, and a rolling
log of usage records. One BillingData document per company (enforced by
a unique index on company_id).
"""

from __future__ import annotations

from datetime import datetime

import mongoengine as me
from src.core.time_utils import utc_now


class BillingAddress(me.EmbeddedDocument):
    line1 = me.StringField(required=True)
    line2 = me.StringField()
    city = me.StringField(required=True)
    region = me.StringField()
    postal_code = me.StringField()
    country = me.StringField(required=True)


class UsageRecord(me.EmbeddedDocument):
    period_start = me.DateTimeField(required=True)
    period_end = me.DateTimeField(required=True)
    route_calculations = me.IntField(default=0)
    ai_reroutes = me.IntField(default=0)
    api_calls = me.IntField(default=0)


class BillingData(me.Document):
    company_id = me.ObjectIdField(required=True, unique=True)
    billing_email = me.EmailField(required=True)
    billing_address = me.EmbeddedDocumentField(BillingAddress)
    vat_number = me.StringField()
    payment_method = me.StringField(
        choices=["card", "wire", "invoice"], default="invoice"
    )
    subscription_tier = me.StringField(
        choices=["trial", "starter", "growth", "enterprise"], default="trial"
    )
    subscription_started_at = me.DateTimeField()
    subscription_renews_at = me.DateTimeField()
    usage = me.EmbeddedDocumentListField(UsageRecord, default=list)
    created_at = me.DateTimeField(default=utc_now)
    updated_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "billing_data",
        "indexes": [
            {"fields": ["company_id"], "unique": True},
            "subscription_tier",
        ],
    }

    def touch(self) -> None:
        self.updated_at = utc_now()
