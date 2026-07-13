import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now

class AuditLog(me.Document):
    event_type = me.StringField(required=True)
    data = me.DictField(default=dict)
    entity_type = me.StringField(choices=["route", "vessel", "zone", "event"])
    entity_id = me.ObjectIdField()
    action = me.StringField(choices=["created", "updated", "deleted", "recalculated", "status_changed"])
    changed_by = me.StringField()
    details = me.DictField(default=dict)
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "audit_logs",
        "indexes": ["event_type", "entity_type", "entity_id", "created_at"]
    }
