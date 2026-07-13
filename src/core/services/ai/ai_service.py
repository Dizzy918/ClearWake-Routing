"""
AI Service — unified façade for all AI capabilities.

Coordinates anomaly detection, ETA prediction, rerouting, and
recommendation generation through a single entry point.  Also serves
as the event handler for the Observer-based event system.
"""

import logging
from datetime import datetime
from typing import Optional

from src.core.services.ai.anomaly_detector import AnomalyDetector
from src.core.services.ai.eta_predictor import ETAPredictor
from src.core.services.ai.recommendation_engine import RecommendationEngine
from src.core.services.ai.reroute_engine import RerouteEngine
from src.infrastructure.repositories.ai_repository import AIRepository
from src.infrastructure.repositories.route_repository import RouteRepository
from src.infrastructure.repositories.vessel_repository import VesselRepository
from src.core.time_utils import utc_now

logger = logging.getLogger(__name__)


class AIService:
    """
    Façade that wires together every AI sub-service and exposes a
    high-level API consumed by routers, observers, and background tasks.
    """

    def __init__(self) -> None:
        self._ai_repo = AIRepository()
        self._route_repo = RouteRepository()
        self._vessel_repo = VesselRepository()

        self._anomaly_detector = AnomalyDetector(
            self._ai_repo, self._vessel_repo, self._route_repo
        )
        self._eta_predictor = ETAPredictor(
            self._ai_repo, self._route_repo, self._vessel_repo
        )
        self._reroute_engine = RerouteEngine(
            self._ai_repo, self._route_repo, self._vessel_repo
        )
        self._recommendation_engine = RecommendationEngine(
            self._ai_repo, self._route_repo, self._vessel_repo
        )

    def handle_reroute_request(
        self,
        vessel_id: str,
        reason: Optional[str] = None,
        current_position: Optional[list] = None,
        force: bool = False,
    ) -> dict:
        """Evaluate and optionally suggest a reroute for *vessel_id*."""
        logger.info(
            "Reroute request for vessel %s (reason=%s, force=%s)",
            vessel_id,
            reason,
            force,
        )
        return self._reroute_engine.evaluate_reroute(
            vessel_id=vessel_id,
            reason=reason,
            current_position=current_position,
            force=force,
        )

    def get_recommendations(
        self,
        vessel_id: Optional[str] = None,
        company_id: Optional[str] = None,
        types: Optional[list] = None,
        priority: Optional[str] = None,
        status: str = "active",
        limit: int = 20,
    ) -> list:
        """Retrieve filtered recommendations from the recommendation engine."""
        return self._recommendation_engine.get_recommendations(
            vessel_id=vessel_id,
            company_id=company_id,
            types=types,
            priority=priority,
            status=status,
            limit=limit,
        )

    def generate_recommendations(
        self,
        vessel_id: Optional[str] = None,
        company_id: Optional[str] = None,
    ) -> list:
        """Run every generator + persist the new recommendations."""
        if not vessel_id and not company_id:
            raise ValueError("vessel_id or company_id is required")
        return self._recommendation_engine.generate_all(
            vessel_id=vessel_id,
            company_id=company_id,
        )

    def apply_reroute(
        self,
        route_id: str,
        new_waypoints: list[dict],
        new_stats: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Persist an accepted reroute by replacing the Route's waypoints and
        stats. Returns the updated route as a dict, or None if not found.
        """
        if not new_waypoints:
            raise ValueError("new_waypoints is required and must be non-empty")

        update_data: dict = {"waypoints": new_waypoints}
        if new_stats:
            for key in ("total_distance_nm", "estimated_duration_h", "estimated_fuel_tons"):
                if key in new_stats and new_stats[key] is not None:
                    update_data[key] = new_stats[key]

        updated = self._route_repo.update(route_id, update_data)
        if updated is None:
            return None

        import json
        result = json.loads(updated.to_json())
        result["applied_at"] = utc_now().isoformat()
        return result

    def run_anomaly_scan(self, vessel_id: str) -> list:
        """
        Run a full anomaly scan for *vessel_id*, persist every finding,
        and return the list of anomaly dicts.
        """
        anomalies = self._anomaly_detector.detect_anomalies(vessel_id)

        for anomaly in anomalies:
            try:
                self._ai_repo.create_anomaly(anomaly)
            except Exception:
                logger.exception(
                    "Failed to persist anomaly %s for vessel %s.",
                    anomaly.get("anomaly_type"),
                    vessel_id,
                )

        return anomalies

    def predict_eta(self, vessel_id: str, route_id: str) -> dict:
        """Predict ETA for *vessel_id* on *route_id*."""
        return self._eta_predictor.predict_eta(vessel_id, route_id)

    def process_event(self, event) -> None:
        """
        Route an incoming domain event to the appropriate AI handler(s).

        Supported event types
        ---------------------
        ZONE_STATUS_CHANGED
            Scan affected vessels and evaluate reroutes.
        VESSEL_ANOMALY
            Log the anomaly and generate related recommendations.
        WEATHER_ALERT
            Generate weather-specific recommendations.
        ROUTE_CALCULATED
            Run a pre-check anomaly scan on the new route's vessel.
        """
        event_type = getattr(event, "event_type", None)
        data = getattr(event, "data", {})

        logger.info("AIService processing event: %s", event_type)

        try:
            if event_type == "ZONE_STATUS_CHANGED":
                self._handle_zone_change(data)
            elif event_type == "VESSEL_ANOMALY":
                self._handle_vessel_anomaly(data)
            elif event_type == "WEATHER_ALERT":
                self._handle_weather_alert(data)
            elif event_type == "ROUTE_CALCULATED":
                self._handle_route_calculated(data)
            else:
                logger.debug("Unhandled event type: %s", event_type)
        except Exception:
            logger.exception("Error processing event %s.", event_type)

    def update_recommendation(self, rec_id: str, data: dict) -> dict:
        """
        Update a recommendation's status or metadata.

        Returns the updated recommendation dict or an error dict.
        """
        try:
            updated = self._ai_repo.update_recommendation(rec_id, data)
            if updated is None:
                return {"error": f"Recommendation {rec_id} not found."}
            if hasattr(updated, "to_mongo"):
                result = updated.to_mongo().to_dict()
                result["_id"] = str(result.get("_id", ""))
                return result
            return updated if isinstance(updated, dict) else {"id": str(rec_id), "status": "updated"}
        except Exception:
            logger.exception("Failed to update recommendation %s.", rec_id)
            return {"error": "Failed to update recommendation."}

    def get_anomalies(
        self,
        vessel_id: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> list:
        """Retrieve anomaly records, optionally filtered by vessel or severity."""
        try:
            return self._ai_repo.get_anomalies(
                vessel_id=vessel_id, severity=severity
            )
        except Exception:
            logger.exception("Failed to fetch anomalies.")
            return []

    def _handle_zone_change(self, data: dict) -> None:
        """Scan all en-route vessels and evaluate reroutes when a zone changes."""
        new_status = data.get("new_status")
        zone_id = data.get("zone_id")

        logger.info(
            "Zone %s changed to '%s' — evaluating affected vessels.",
            zone_id,
            new_status,
        )

        try:
            en_route_vessels = self._vessel_repo.get_by_status("en_route")
        except Exception:
            logger.exception("Failed to fetch en-route vessels.")
            return

        for vessel in en_route_vessels:
            try:
                self._reroute_engine.evaluate_reroute(
                    vessel_id=str(vessel.id),
                    reason="zone_closed" if new_status == "active" else "zone_reopened",
                )
            except Exception:
                logger.exception(
                    "Reroute evaluation failed for vessel %s after zone change.",
                    vessel.id,
                )

    def _handle_vessel_anomaly(self, data: dict) -> None:
        """Log the anomaly and generate related recommendations."""
        vessel_id = data.get("vessel_id")
        if not vessel_id:
            return

        try:
            self._ai_repo.create_anomaly(data)
        except Exception:
            logger.exception("Failed to persist external anomaly for vessel %s.", vessel_id)

        try:
            self._recommendation_engine.generate_all(vessel_id=vessel_id)
        except Exception:
            logger.exception(
                "Recommendation generation failed after anomaly for vessel %s.",
                vessel_id,
            )

    def _handle_weather_alert(self, data: dict) -> None:
        """Generate weather recommendations for all en-route vessels."""
        try:
            en_route_vessels = self._vessel_repo.get_by_status("en_route")
        except Exception:
            logger.exception("Failed to fetch en-route vessels for weather alert.")
            return

        for vessel in en_route_vessels:
            try:
                self._recommendation_engine.generate_all(vessel_id=str(vessel.id))
            except Exception:
                logger.exception(
                    "Weather recommendation generation failed for vessel %s.",
                    vessel.id,
                )

    def _handle_route_calculated(self, data: dict) -> None:
        """Run a pre-check anomaly scan on the vessel of a newly calculated route."""
        vessel_id = data.get("vessel_id")
        if not vessel_id:
            return

        try:
            self.run_anomaly_scan(str(vessel_id))
        except Exception:
            logger.exception(
                "Pre-check anomaly scan failed for vessel %s.", vessel_id
            )
