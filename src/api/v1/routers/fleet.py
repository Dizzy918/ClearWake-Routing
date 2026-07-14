"""
Fleet API — company-scoped vessel CRUD.

Thin wrapper over /api/v1/vessels that:
  * always scopes list operations by company,
  * validates ObjectIds at the edge so callers get 400 instead of 500,
  * reuses VesselRepository and VesselStatusService.
"""

import json
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access

from src.core.services.vessel_status_service import VesselStatusService
from src.infrastructure.repositories.vessel_repository import VesselRepository
from src.models.user import User
from src.models.vessel import Vessel as VesselModel
from src.schemas.vessel import VesselCreateSchema, VesselUpdateSchema

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"], dependencies=[Depends(get_current_user)])
_repo = VesselRepository()
_status = VesselStatusService(_repo)


def _serialize(vessel) -> dict:
    return json.loads(vessel.to_json())


def _require_company(company_id: Optional[str], user: User) -> ObjectId:
    if not company_id:
        raise HTTPException(status_code=400, detail="company_id is required")
    if not ObjectId.is_valid(company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    require_company_access(user, company_id)
    return ObjectId(company_id)


@router.get("/vessels")
def list_fleet_vessels(
    company_id: str = Query(..., description="Owner company ObjectId"),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
):
    cid = _require_company(company_id, user)
    query = {"company_id": cid}
    if status:
        query["current_status"] = status
    vessels = list(VesselModel.objects(**query).limit(limit))
    return [_serialize(v) for v in vessels]


@router.get("/vessels/{vessel_id}")
def get_fleet_vessel(
    vessel_id: str,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    cid = _require_company(company_id, user)
    if not ObjectId.is_valid(vessel_id):
        raise HTTPException(status_code=400, detail="Invalid vessel_id")
    vessel = _repo.get_by_id(vessel_id)
    if not vessel or vessel.company_id != cid:
        raise HTTPException(status_code=404, detail="Vessel not found")
    return _serialize(vessel)


@router.post("/vessels", status_code=201)
def create_fleet_vessel(vessel_in: VesselCreateSchema, user: User = Depends(get_current_user)):
    payload = vessel_in.model_dump(exclude_unset=True)
    cid = _require_company(payload.get("company_id"), user)
    payload["company_id"] = cid
    vessel = VesselModel.build(**payload)
    created = _repo.create(vessel)
    return _serialize(created)


@router.put("/vessels/{vessel_id}")
def replace_fleet_vessel(
    vessel_id: str,
    vessel_in: VesselUpdateSchema,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    cid = _require_company(company_id, user)
    if not ObjectId.is_valid(vessel_id):
        raise HTTPException(status_code=400, detail="Invalid vessel_id")
    existing = _repo.get_by_id(vessel_id)
    if not existing or existing.company_id != cid:
        raise HTTPException(status_code=404, detail="Vessel not found")
    updated = _status.update_vessel(vessel_id, vessel_in.model_dump(exclude_unset=True))
    return _serialize(updated)


@router.delete("/vessels/{vessel_id}", status_code=204)
def delete_fleet_vessel(
    vessel_id: str,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    cid = _require_company(company_id, user)
    if not ObjectId.is_valid(vessel_id):
        raise HTTPException(status_code=400, detail="Invalid vessel_id")
    existing = _repo.get_by_id(vessel_id)
    if not existing or existing.company_id != cid:
        raise HTTPException(status_code=404, detail="Vessel not found")
    _repo.delete(vessel_id)
    return None
