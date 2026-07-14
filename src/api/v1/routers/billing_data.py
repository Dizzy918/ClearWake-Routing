import json

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.infrastructure.repositories.billing_data_repository import (
    BillingDataRepository,
)
from src.schemas.billing_data import (
    BillingDataCreateSchema,
    BillingDataUpdateSchema,
    UsageRecordSchema,
)

router = APIRouter(prefix="/api/v1/billing-data", tags=["billing-data"], dependencies=[Depends(get_current_user)])
repo = BillingDataRepository()


@router.get("/")
def get_billing(company_id: str = Query(...), user: User = Depends(get_current_user)):
    require_company_access(user, company_id)
    record = repo.get_for_company(company_id)
    if not record:
        raise HTTPException(status_code=404, detail="Billing data not found")
    return json.loads(record.to_json())


@router.post("/")
def create_billing(payload: BillingDataCreateSchema, user: User = Depends(get_current_user)):
    if not ObjectId.is_valid(payload.company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    require_company_access(user, payload.company_id)
    if repo.get_for_company(payload.company_id):
        raise HTTPException(
            status_code=409,
            detail="Billing data already exists for this company",
        )

    data = payload.model_dump()
    data["company_id"] = ObjectId(payload.company_id)
    created = repo.create(data)
    return json.loads(created.to_json())


@router.patch("/")
def update_billing(
    payload: BillingDataUpdateSchema,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    data = payload.model_dump(exclude_unset=True)
    updated = repo.update(company_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Billing data not found")
    return json.loads(updated.to_json())


@router.delete("/")
def delete_billing(company_id: str = Query(...), user: User = Depends(get_current_user)):
    require_company_access(user, company_id)
    if not repo.delete(company_id):
        raise HTTPException(status_code=404, detail="Billing data not found")
    return {"deleted": True}


@router.post("/usage")
def append_usage(
    payload: UsageRecordSchema,
    company_id: str = Query(...),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    updated = repo.append_usage(company_id, payload.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Billing data not found")
    return json.loads(updated.to_json())
