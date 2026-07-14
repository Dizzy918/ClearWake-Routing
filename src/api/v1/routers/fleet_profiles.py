import json

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.infrastructure.repositories.fleet_profile_repository import (
    FleetProfileRepository,
)
from src.schemas.fleet_profile import (
    FleetProfileCreateSchema,
    FleetProfileUpdateSchema,
)

router = APIRouter(prefix="/api/v1/fleet-profiles", tags=["fleet-profiles"], dependencies=[Depends(get_current_user)])
repo = FleetProfileRepository()


def _to_object_ids(raw_ids: list[str]) -> list[ObjectId]:
    out: list[ObjectId] = []
    for value in raw_ids:
        if not ObjectId.is_valid(value):
            raise HTTPException(status_code=400, detail=f"Invalid id '{value}'")
        out.append(ObjectId(value))
    return out


@router.get("/")
def list_fleet_profiles(company_id: str = Query(...), user: User = Depends(get_current_user)):
    require_company_access(user, company_id)
    profiles = repo.list_for_company(company_id)
    return [json.loads(p.to_json()) for p in profiles]


@router.get("/{profile_id}")
def get_fleet_profile(
    profile_id: str,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    profile = repo.get_by_id(profile_id, company_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Fleet profile not found")
    return json.loads(profile.to_json())


@router.post("/")
def create_fleet_profile(payload: FleetProfileCreateSchema, user: User = Depends(get_current_user)):
    if not ObjectId.is_valid(payload.company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    require_company_access(user, payload.company_id)

    data = payload.model_dump()
    data["company_id"] = ObjectId(payload.company_id)
    data["vessel_ids"] = _to_object_ids(payload.vessel_ids)
    data["preferred_route_ids"] = _to_object_ids(payload.preferred_route_ids)
    created = repo.create(data)
    return json.loads(created.to_json())


@router.patch("/{profile_id}")
def update_fleet_profile(
    profile_id: str,
    payload: FleetProfileUpdateSchema,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    data = payload.model_dump(exclude_unset=True)
    if "vessel_ids" in data and data["vessel_ids"] is not None:
        data["vessel_ids"] = _to_object_ids(data["vessel_ids"])
    if "preferred_route_ids" in data and data["preferred_route_ids"] is not None:
        data["preferred_route_ids"] = _to_object_ids(data["preferred_route_ids"])

    updated = repo.update(profile_id, company_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Fleet profile not found")
    return json.loads(updated.to_json())


@router.delete("/{profile_id}")
def delete_fleet_profile(
    profile_id: str,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    if not repo.delete(profile_id, company_id):
        raise HTTPException(status_code=404, detail="Fleet profile not found")
    return {"deleted": True}
