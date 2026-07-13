from typing import List, Optional
from datetime import datetime

from src.core.time_utils import utc_now
from src.models.ai_models import (
    AnomalyRecord,
    RerouteLog,
    AIRecommendation,
    ETAPrediction,
)


class AIRepository:

    def create_anomaly(self, data: dict) -> AnomalyRecord:
        anomaly = AnomalyRecord(**data)
        anomaly.save()
        return anomaly

    def get_anomalies(
        self,
        vessel_id: Optional[str] = None,
        severity: Optional[str] = None,
        resolved: Optional[bool] = None,
    ) -> List[AnomalyRecord]:
        filters = {}
        if vessel_id is not None:
            filters["vessel_id"] = vessel_id
        if severity is not None:
            filters["severity"] = severity
        if resolved is not None:
            filters["resolved"] = resolved
        return list(
            AnomalyRecord.objects(**filters).order_by("-detected_at")
        )

    def resolve_anomaly(self, anomaly_id: str) -> Optional[AnomalyRecord]:
        anomaly = AnomalyRecord.objects(id=anomaly_id).first()
        if not anomaly:
            return None
        anomaly.update(resolved=True, resolved_at=utc_now())
        anomaly.reload()
        return anomaly

    def create_reroute_log(self, data: dict) -> RerouteLog:
        log = RerouteLog(**data)
        log.save()
        return log

    def get_reroute_logs(
        self,
        vessel_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[RerouteLog]:
        filters = {}
        if vessel_id is not None:
            filters["vessel_id"] = vessel_id
        if status is not None:
            filters["status"] = status
        return list(
            RerouteLog.objects(**filters).order_by("-created_at")
        )

    def update_reroute_status(
        self, log_id: str, status: str
    ) -> Optional[RerouteLog]:
        log = RerouteLog.objects(id=log_id).first()
        if not log:
            return None
        log.update(status=status)
        log.reload()
        return log

    def create_recommendation(self, data: dict) -> AIRecommendation:
        rec = AIRecommendation(**data)
        rec.save()
        return rec

    def get_recommendations(
        self,
        vessel_id: Optional[str] = None,
        company_id: Optional[str] = None,
        types: Optional[List[str]] = None,
        priority: Optional[str] = None,
        status: str = "active",
        limit: int = 20,
    ) -> List[AIRecommendation]:
        filters = {"status": status}
        if vessel_id is not None:
            filters["vessel_id"] = vessel_id
        if company_id is not None:
            filters["company_id"] = company_id
        if types is not None:
            filters["recommendation_type__in"] = types
        if priority is not None:
            filters["priority"] = priority
        return list(
            AIRecommendation.objects(**filters)
            .order_by("-created_at")
            .limit(limit)
        )

    def update_recommendation(
        self, rec_id: str, data: dict
    ) -> Optional[AIRecommendation]:
        rec = AIRecommendation.objects(id=rec_id).first()
        if not rec:
            return None
        rec.update(**data)
        rec.reload()
        return rec

    def create_eta_prediction(self, data: dict) -> ETAPrediction:
        prediction = ETAPrediction(**data)
        prediction.save()
        return prediction

    def get_latest_prediction(
        self, vessel_id: str, route_id: str
    ) -> Optional[ETAPrediction]:
        return (
            ETAPrediction.objects(vessel_id=vessel_id, route_id=route_id)
            .order_by("-created_at")
            .first()
        )
