import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now

class ApiKey(me.EmbeddedDocument):
    key_hash = me.StringField(required=True)
    label = me.StringField(required=True)
    is_active = me.BooleanField(default=True)
    expires_at = me.DateTimeField()

class Company(me.Document):
    name = me.StringField(required=True)
    email = me.StringField(required=True, unique=True)
    status = me.StringField(choices=["active", "suspended", "trial"], default="trial")
    api_keys = me.EmbeddedDocumentListField(ApiKey, default=list)
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "companies",
        "indexes": ["email", "name"]
    }
