import mongoengine as me
from datetime import datetime
from src.core.time_utils import utc_now


class AnomalyRecord(me.Document):
    """Detected anomaly for a vessel (speed, course, fuel, zone, comms)."""
    vessel_id = me.ObjectIdField(required=True)
    anomaly_type = me.StringField(
        choices=[
            "speed_deviation",
            "course_deviation",
            "fuel_anomaly",
            "zone_violation",
            "communication_loss",
        ]
    )
    severity = me.StringField(choices=["low", "medium", "high", "critical"])
    details = me.DictField(default=dict)
    detected_at = me.DateTimeField(default=utc_now)
    resolved = me.BooleanField(default=False)
    resolved_at = me.DateTimeField(null=True)

    meta = {
        "collection": "ai_anomalies",
        "indexes": ["vessel_id", "anomaly_type", "severity", "detected_at"],
    }


class RerouteLog(me.Document):
    """Log entry for an AI-triggered reroute suggestion or application."""
    vessel_id = me.ObjectIdField(required=True)
    original_route_id = me.ObjectIdField()
    new_route_id = me.ObjectIdField(null=True)
    reason = me.StringField(required=True)
    trigger_event = me.StringField()
    original_eta_h = me.FloatField()
    new_eta_h = me.FloatField()
    fuel_delta_tons = me.FloatField()
    distance_delta_nm = me.FloatField()
    status = me.StringField(
        choices=["suggested", "accepted", "rejected", "auto_applied"],
        default="suggested",
    )
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "ai_reroute_logs",
        "indexes": ["vessel_id", "status", "created_at"],
    }


class AIRecommendation(me.Document):
    """Actionable AI recommendation (vessel-specific or fleet-wide)."""
    vessel_id = me.ObjectIdField(null=True)
    company_id = me.ObjectIdField(null=True)
    recommendation_type = me.StringField(
        required=True,
        choices=[
            "route_optimization",
            "speed_adjustment",
            "fuel_saving",
            "zone_avoidance",
            "weather_routing",
        ],
    )
    title = me.StringField(required=True)
    description = me.StringField()
    data = me.DictField(default=dict)
    confidence = me.FloatField()
    priority = me.StringField(
        choices=["low", "medium", "high", "urgent"],
        default="medium",
    )
    status = me.StringField(
        choices=["active", "dismissed", "applied", "expired"],
        default="active",
    )
    expires_at = me.DateTimeField(null=True)
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "ai_recommendations",
        "indexes": [
            "vessel_id",
            "company_id",
            "recommendation_type",
            "priority",
            "status",
            "created_at",
        ],
    }


class ETAPrediction(me.Document):
    """AI-generated ETA prediction for a vessel on a given route."""
    vessel_id = me.ObjectIdField(required=True)
    route_id = me.ObjectIdField(required=True)
    original_eta_h = me.FloatField(required=True)
    predicted_eta_h = me.FloatField(required=True)
    confidence = me.FloatField()
    factors = me.DictField(default=dict)
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "ai_eta_predictions",
        "indexes": ["vessel_id", "route_id", "created_at"],
    }
