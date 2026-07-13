import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now

class RouteRequest(me.Document):
    company_id = me.ObjectIdField(required=True)
    vessel_id = me.ObjectIdField(required=True)
    origin = me.PointField(required=True)
    destination = me.PointField(required=True)
    optimization_mode = me.StringField(choices=["fastest", "eco"], required=True)
    status = me.StringField(choices=["pending", "processing", "completed", "failed"], default="pending")
    requested_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "route_requests",
        "indexes": ["company_id", "status"]
    }
