"""Analytics router."""

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.core.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"], dependencies=[Depends(get_current_user)])
_service = AnalyticsService()


@router.get("/vessels/{vessel_id}")
def vessel_summary(vessel_id: str, limit: int = Query(default=200, ge=1, le=1000)):
    try:
        return _service.vessel_summary(vessel_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/companies/{company_id}")
def company_summary(
    company_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(get_current_user),
):
    require_company_access(user, company_id)
    try:
        return _service.company_summary(company_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/strategy-effectiveness")
def strategy_effectiveness(limit: int = Query(default=1000, ge=1, le=5000)):
    try:
        return _service.strategy_effectiveness(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
