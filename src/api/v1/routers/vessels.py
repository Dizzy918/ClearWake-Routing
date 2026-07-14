import json
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access

from src.core.services.vessel_status_service import VesselStatusService
from src.infrastructure.repositories.vessel_repository import VesselRepository
from src.models.user import User
from src.models.vessel import VESSEL_TYPE_OPTIONS, Vessel as VesselModel
from src.schemas.vessel import VesselCreateSchema, VesselUpdateSchema

router = APIRouter(prefix="/api/v1/vessels", tags=["vessels"], dependencies=[Depends(get_current_user)])
repo = VesselRepository()
status_service = VesselStatusService(repo)


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
