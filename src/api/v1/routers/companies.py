import json

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.infrastructure.repositories.company_repository import CompanyRepository
from src.models.company import Company as CompanyModel
from src.schemas.company import CompanyCreateSchema, CompanyUpdateSchema

router = APIRouter(prefix="/api/v1/companies", tags=["companies"], dependencies=[Depends(get_current_user)])
repo = CompanyRepository()


@router.get("/")
def get_all_companies(user: User = Depends(get_current_user)):
    """List companies visible to the caller — i.e. their own tenant."""
    company = repo.get_by_id(str(user.company_id))
    return [json.loads(company.to_json())] if company else []


@router.get("/{company_id}")
def get_company_by_id(company_id: str, user: User = Depends(get_current_user)):
    require_company_access(user, company_id)
    company = repo.get_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return json.loads(company.to_json())


@router.post("/")
def create_company(company_in: CompanyCreateSchema):
    company = CompanyModel(**company_in.model_dump(exclude_unset=True))
    created = repo.create(company)
    return json.loads(created.to_json())


@router.patch("/{company_id}")
def update_company(
    company_id: str,
    company_in: CompanyUpdateSchema,
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    updated = repo.update(company_id, company_in.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Company not found")
    return json.loads(updated.to_json())


@router.delete("/{company_id}")
def delete_company(company_id: str, user: User = Depends(get_current_user)):
    require_company_access(user, company_id)
    if not repo.delete(company_id):
        raise HTTPException(status_code=404, detail="Company not found")
    return {"deleted": True}
