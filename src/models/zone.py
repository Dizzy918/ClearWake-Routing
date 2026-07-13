import mongoengine as me
from datetime import datetime
from src.core.events.event import Event
from src.core.time_utils import utc_now

class Zone(me.Document):
    name = me.StringField(required=True)
    zone_type = me.StringField(required=True, choices=["eco", "conflict", "temporary", "canal"])
    status = me.StringField(choices=["active", "inactive"], default="active")
    geometry = me.PolygonField(required=True)
    description = me.StringField()
    valid_from = me.DateTimeField()
    valid_until = me.DateTimeField()
    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "zones",
        "indexes": [
            "zone_type",
            "status",
            {"fields": ["geometry"], "cls": False}
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
