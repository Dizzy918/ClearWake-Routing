import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now

class Event(me.Document):
    event_type = me.StringField(required=True, choices=[
        "zone_closed", "zone_opened", "storm", "canal_blocked",
        "vessel_anomaly", "reroute_triggered", "reroute_accepted",
        "eta_deviation", "ai_recommendation", "weather_alert",
    ])
    zone_id = me.ObjectIdField()
    affected_routes = me.ListField(me.ObjectIdField(), default=list)
    payload = me.DictField(default=dict)
    status = me.StringField(choices=["pending", "dispatched", "resolved"], default="pending")
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "events",
        "indexes": ["event_type", "status", "created_at"]
    }
