import mongoengine as me
from datetime import datetime

# Kinds of area a route may have to respect. The first four are the original
# set; the rest exist so real-world hazard feeds import into something
# meaningful rather than all landing in "temporary".
ZONE_TYPES: tuple[str, ...] = (
    "eco",             # protected / ecologically sensitive
    "conflict",        # war risk, active hostilities
    "temporary",       # short-lived closure
    "canal",           # managed passage with its own constraints
    "piracy",          # reported attacks / high-risk area
    "military",        # exercises, live firing, naval restrictions
    "restricted",      # territorial or administrative restriction
    "hazard",          # wreck, obstruction, drifting object, unlit buoy
    "environmental",   # pollution, ice, volcanic ash
)
from src.core.events.dispatcher import dispatcher
from src.core.events.event import Event
from src.core.time_utils import utc_now

class Zone(me.Document):
    name = me.StringField(required=True)
    zone_type = me.StringField(required=True, choices=ZONE_TYPES)
    status = me.StringField(choices=["active", "inactive"], default="active")
    geometry = me.PolygonField(required=True)
    description = me.StringField()
    valid_from = me.DateTimeField()
    valid_until = me.DateTimeField()
    created_at = me.DateTimeField(default=utc_now)

    # ---- tenancy ----
    # None means a shared zone: official closures, imported hazards, and the
    # chokepoint presets, which are facts about the sea and apply to everyone.
    # A company_id makes the zone private to that operator, so one customer
    # cannot reroute another customer's fleet by drawing a polygon.
    company_id = me.ObjectIdField()

    # ---- provenance ----
    # Set when a zone comes from a public feed rather than being drawn by an
    # operator. external_id makes re-importing idempotent, and keeps imported
    # areas distinguishable from ones the company drew itself.
    source = me.StringField()
    external_id = me.StringField()
    source_url = me.StringField()
    imported_at = me.DateTimeField()

    meta = {
        "collection": "zones",
        "indexes": [
            "zone_type",
            "status",
            {"fields": ["geometry"], "cls": False},
            {"fields": ["source", "external_id"], "sparse": True},
            {"fields": ["company_id", "status"], "sparse": True},
        ]
    }

    def update_status(self, new_status: str):
        old_status = self.status
        self.status = new_status
        self.save()

        event = Event(
            event_type="ZONE_STATUS_CHANGED",
            data={
                "zone_id": str(self.id),
                "old_status": old_status,
                "new_status": new_status
            }
        )

        dispatcher.dispatch(event)
