import json

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_dependencies import get_current_user

from src.models.event import Event
from src.schemas.event import EventCreateSchema, EventUpdateSchema

router = APIRouter(prefix="/api/v1/events", tags=["events"], dependencies=[Depends(get_current_user)])


@router.get("/")
def get_all_events():
    events = Event.objects.all()
    return [json.loads(event.to_json()) for event in events]


@router.get("/{event_id}")
def get_event_by_id(event_id: str):
    event = Event.objects(id=event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return json.loads(event.to_json())


@router.post("/")
def create_event(event_in: EventCreateSchema):
    event = Event(**event_in.model_dump(exclude_unset=True))
    event.save()
    return json.loads(event.to_json())


@router.patch("/{event_id}")
def update_event(event_id: str, event_in: EventUpdateSchema):
    event = Event.objects(id=event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    update_data = event_in.model_dump(exclude_unset=True)
    if update_data:
        event.update(**update_data)
        event.reload()

    return json.loads(event.to_json())


@router.delete("/{event_id}")
def delete_event(event_id: str):
    event = Event.objects(id=event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.delete()
    return {"deleted": True}
