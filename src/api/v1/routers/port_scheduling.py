"""
Port scheduling router.

Endpoints
---------
GET    /api/v1/ports
POST   /api/v1/ports
DELETE /api/v1/ports/{port_id}

GET    /api/v1/ports/{port_id}/schedule
PUT    /api/v1/ports/{port_id}/schedule

GET    /api/v1/dock-reservations
POST   /api/v1/dock-reservations
GET    /api/v1/dock-reservations/{reservation_id}
DELETE /api/v1/dock-reservations/{reservation_id}   (sets status=cancelled)
"""

from __future__ import annotations

import json
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.infrastructure.repositories.port_scheduling_repository import (
    DockReservationRepository,
    PortRepository,
    PortScheduleRepository,
)
from src.models.port_scheduling import DockReservation
from src.schemas.port_scheduling import (
    DockReservationCreateSchema,
    PortCreateSchema,
    PortScheduleSchema,
)

router = APIRouter(prefix="/api/v1", tags=["port-scheduling"], dependencies=[Depends(get_current_user)])
_ports = PortRepository()
_schedules = PortScheduleRepository()
_reservations = DockReservationRepository()


def _to_dict(doc) -> dict:
    return json.loads(doc.to_json())



@router.get("/ports")
def list_ports(limit: int = Query(default=500, ge=1, le=2000)):
    return [_to_dict(p) for p in _ports.list_all(limit=limit)]


@router.post("/ports", status_code=201)
def create_port(payload: PortCreateSchema):
    existing = _ports.get_by_port_id(payload.port_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="port_id already exists")
    created = _ports.create(payload.model_dump())
    return _to_dict(created)


@router.get("/ports/{port_id}")
def get_port(port_id: str):
    port = _ports.get_by_port_id(port_id)
    if port is None:
        raise HTTPException(status_code=404, detail="Port not found")
    return _to_dict(port)


@router.delete("/ports/{port_id}", status_code=204)
def delete_port(port_id: str):
    if not _ports.delete(port_id):
        raise HTTPException(status_code=404, detail="Port not found")
    return None



@router.get("/ports/{port_id}/schedule")
def get_port_schedule(port_id: str):
    schedule = _schedules.get(port_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _to_dict(schedule)


@router.put("/ports/{port_id}/schedule")
def upsert_port_schedule(port_id: str, payload: PortScheduleSchema):
    data = payload.model_dump()
    data["blackouts"] = [b for b in data.get("blackouts", [])]
    schedule = _schedules.upsert(port_id, data)
    return _to_dict(schedule)



@router.get("/dock-reservations")
def list_reservations(
    port_id: str = Query(...),
    berth_number: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    items = _reservations.list_for_port(port_id, berth_number=berth_number, limit=limit)
    return [_to_dict(r) for r in items]


@router.post("/dock-reservations", status_code=201)
def create_reservation(
    payload: DockReservationCreateSchema,
    user: User = Depends(get_current_user),
):
    if not ObjectId.is_valid(payload.vessel_id):
        raise HTTPException(status_code=400, detail="Invalid vessel_id")
    if payload.company_id and not ObjectId.is_valid(payload.company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    require_company_access(user, payload.company_id)

    candidate = DockReservation(
        port_id=payload.port_id,
        berth_number=payload.berth_number,
        vessel_id=ObjectId(payload.vessel_id),
        company_id=ObjectId(payload.company_id) if payload.company_id else None,
        start_at=payload.start_at,
        end_at=payload.end_at,
        purpose=payload.purpose,
        notes=payload.notes,
    )

    conflicts = _reservations.find_conflicts(candidate)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Reservation conflicts with existing slot(s)",
                "conflicts": [_to_dict(c) for c in conflicts],
            },
        )

    created = _reservations.create(candidate.to_mongo().to_dict())
    return _to_dict(created)


@router.get("/dock-reservations/{reservation_id}")
def get_reservation(reservation_id: str):
    reservation = _reservations.get_by_id(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return _to_dict(reservation)


@router.delete("/dock-reservations/{reservation_id}")
def cancel_reservation(reservation_id: str):
    cancelled = _reservations.cancel(reservation_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return _to_dict(cancelled)
