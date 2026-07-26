import json
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access, require_role

from src.core.services.vessel_status_service import VesselStatusService
from src.core.services.vessel_tracking_service import ImplausibleJump, VesselTrackingService
from src.core.services.vessel_course_service import CourseError, VesselCourseService
from src.infrastructure.repositories.vessel_repository import VesselRepository
from src.models.user import User
from src.models.vessel import VESSEL_TYPE_OPTIONS, Vessel as VesselModel
from src.schemas.vessel import (
    VesselCourseSchema,
    VesselCreateSchema,
    VesselPositionSchema,
    VesselUpdateSchema,
)

router = APIRouter(prefix="/api/v1/vessels", tags=["vessels"], dependencies=[Depends(get_current_user)])
repo = VesselRepository()
status_service = VesselStatusService(repo)
tracking_service = VesselTrackingService()
course_service = VesselCourseService()


def _get_own_vessel(vessel_id: str, user: User) -> VesselModel:
    """Fetch a vessel, hiding other tenants' vessels behind a 404."""
    vessel = repo.get_by_id(vessel_id)
    if not vessel or vessel.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Vessel not found")
    return vessel


@router.get("/types")
def get_vessel_types():
    return VESSEL_TYPE_OPTIONS


@router.get("/")
def get_all_vessels(
    company_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    vessels = repo.get_by_company(str(user.company_id))
    if status:
        vessels = [v for v in vessels if v.current_status == status]
    return [json.loads(vessel.to_json()) for vessel in vessels]


@router.get("/{vessel_id}")
def get_vessel_by_id(vessel_id: str, user: User = Depends(get_current_user)):
    vessel = _get_own_vessel(vessel_id, user)
    return json.loads(vessel.to_json())


@router.post("/")
def create_vessel(vessel_in: VesselCreateSchema, user: User = Depends(get_current_user)):
    payload = vessel_in.model_dump(exclude_unset=True)

    if not ObjectId.is_valid(payload["company_id"]):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    require_company_access(user, payload["company_id"])

    payload["company_id"] = ObjectId(payload["company_id"])
    vessel = VesselModel.build(**payload)
    created = repo.create(vessel)
    return json.loads(created.to_json())


@router.patch("/{vessel_id}")
def update_vessel(
    vessel_id: str,
    vessel_in: VesselUpdateSchema,
    user: User = Depends(get_current_user),
):
    _get_own_vessel(vessel_id, user)
    payload = vessel_in.model_dump(exclude_unset=True)

    if "company_id" in payload:
        if not ObjectId.is_valid(payload["company_id"]):
            raise HTTPException(status_code=400, detail="Invalid company_id")
        require_company_access(user, payload["company_id"])
        payload["company_id"] = ObjectId(payload["company_id"])

    updated = status_service.update_vessel(vessel_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Vessel not found")
    return json.loads(updated.to_json())


@router.delete("/{vessel_id}")
def delete_vessel(vessel_id: str, user: User = Depends(get_current_user)):
    _get_own_vessel(vessel_id, user)
    if not repo.delete(vessel_id):
        raise HTTPException(status_code=404, detail="Vessel not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Live tracking
# ---------------------------------------------------------------------------

@router.get("/positions/latest")
def get_fleet_positions(user: User = Depends(get_current_user)):
    """Current position of every vessel in the caller's fleet.

    One query for the whole fleet — the map calls this on load and then keeps
    itself up to date from the websocket rather than polling.
    """
    return tracking_service.latest_for_company(str(user.company_id))


@router.post("/{vessel_id}/position")
def report_position(
    vessel_id: str,
    report: VesselPositionSchema,
    user: User = Depends(require_role("admin", "operator")),
):
    """Record a position fix for a vessel.

    This is the ingest point for AIS/GPS feeds as well as manual entry.
    Implausible jumps are rejected rather than stored: a bad fix that lands
    a ship inland would otherwise corrupt the track and any route planned
    from it.
    """
    vessel = _get_own_vessel(vessel_id, user)
    try:
        fix = tracking_service.record_position(
            vessel,
            longitude=report.lon,
            latitude=report.lat,
            heading_deg=report.heading_deg,
            speed_knots=report.speed_knots,
            source=report.source,
        )
    except ImplausibleJump as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return fix.to_dict()


@router.get("/{vessel_id}/track")
def get_vessel_track(
    vessel_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    user: User = Depends(get_current_user),
):
    """Recent position history, newest first — for track playback."""
    _get_own_vessel(vessel_id, user)
    return [fix.to_dict() for fix in tracking_service.track_for(vessel_id, limit=limit)]


# ---------------------------------------------------------------------------
# Course control
# ---------------------------------------------------------------------------

@router.post("/{vessel_id}/course")
def set_vessel_course(
    vessel_id: str,
    command: VesselCourseSchema,
    user: User = Depends(require_role("admin", "operator")),
):
    """Assign or change a vessel's course from the operations desk.

    Restricted to admins and operators — a viewer can watch the fleet but
    must not be able to redirect a ship. Every change is written to the audit
    log and pushed to the company's dashboards.
    """
    vessel = _get_own_vessel(vessel_id, user)
    try:
        result = course_service.set_course(
            vessel=vessel,
            destination_port=command.destination_port,
            strategy=command.strategy,
            actor=user,
            reason=command.reason,
        )
    except CourseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@router.delete("/{vessel_id}/course")
def clear_vessel_course(
    vessel_id: str,
    user: User = Depends(require_role("admin", "operator")),
):
    """End the current voyage and return the vessel to idle."""
    vessel = _get_own_vessel(vessel_id, user)
    return course_service.clear_course(vessel, actor=user)
