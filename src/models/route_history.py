import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now


class RouteHistory(me.Document):
    """Stores a historical snapshot of every computed route for analytics and auditing."""

    route_id = me.ObjectIdField()
    vessel_id = me.ObjectIdField()
    company_id = me.ObjectIdField()
    origin_port = me.StringField()
    destination_port = me.StringField()
    optimization_mode = me.StringField(choices=["fastest", "eco"])
    total_distance_nm = me.FloatField()
    estimated_duration_h = me.FloatField()
    estimated_fuel_tons = me.FloatField()
    waypoint_count = me.IntField()
    weather_conditions = me.DictField()
    current_conditions = me.DictField()
    calculated_at = me.DateTimeField(default=utc_now)
    status = me.StringField(
        choices=["completed", "cancelled", "in_progress"],
        default="completed",
    )

    meta = {
        "collection": "route_history",
        "indexes": ["vessel_id", "company_id", "-calculated_at"],
        "ordering": ["-calculated_at"],
        # Older bootstrap seeds wrote additional fields (strategy,
        # actual_fuel_tons, calculated_time_h, origin, destination, …).
        # strict=False so those legacy docs still load via mongoengine.
        "strict": False,
    }
