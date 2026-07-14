import json
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User

from src.core.services.ai.ai_service import AIService
from src.core.services.ai.ws_manager import WebSocketManager
from src.schemas.ai import (
    RerouteRequest,
    RerouteResponse,
    ReroutePreviewRequest,
    ApplyRerouteRequest,
    GenerateRecommendationsRequest,
    RecommendationOut,
    RecommendationUpdate,
    AnomalyOut,
    ETAPredictionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"], dependencies=[Depends(get_current_user)])

_ai_service = None
ws_manager = WebSocketManager()


def _get_ai_service() -> AIService:
    """Lazy-initialize the AIService singleton."""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service



@router.post("/reroute", response_model=RerouteResponse)
def request_reroute(request: RerouteRequest):
    """
    Request an AI-powered reroute for a vessel.

    Evaluates the vessel's current route against alternatives and returns
    the best option with comparison statistics (distance, ETA, fuel deltas).
    """
    service = _get_ai_service()

    try:
        result = service.handle_reroute_request(
            vessel_id=request.vessel_id,
            reason=request.reason,
            current_position=request.current_position,
            force=request.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Reroute error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Reroute evaluation failed: {str(exc)}")

    return result



@router.post("/reroute/preview")
async def preview_reroute(request: ReroutePreviewRequest):
    """
    Stateless reroute preview.

    Re-runs the routing strategy against the supplied origin/destination
    with ocean-current and weather penalties layered on top, then returns
    the alternative route + deltas vs the user-supplied current stats.
    Works for the on-screen map route — no DB-backed vessel or persisted
    route required.
    """
    # Local imports avoid hard-loading the graph at module import time.
    from src.api.v1.routers.routes import (
        _resolve_node_id,
        _build_vessel_constraints,
        _compute_route_stats,
        _GRAPH,
    )
    from src.core.routing.strategy import (
        EcoStrategy,
        FastestStrategy,
        CurrentAwareStrategy,
    )

    try:
        start_id = _resolve_node_id(request.start_node_id)
        end_id = _resolve_node_id(request.end_node_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not resolve ports: {exc}")

    vessel = _build_vessel_constraints(
        request.vessel_id or "",
        request.vessel_type,
    )

    mode = (request.optimization_mode or "fastest").lower()
    if mode == "eco":
        base = EcoStrategy()
    elif mode == "fastest":
        base = FastestStrategy()
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid optimization_mode. Use 'fastest' or 'eco'.",
        )

    # ── Sample weather + currents along the great-circle midpoint band ──
    # We sample a tiny grid (~10 points) so the call stays fast. The
    # samples drive WeatherPenalty + CurrentBoost on the matching edges.
    weather_data: dict[tuple[float, float], float] = {}
    current_data: dict[tuple[float, float], tuple[float, float]] = {}

    try:
        start_wp = _GRAPH.get_waypoint(start_id)
        end_wp = _GRAPH.get_waypoint(end_id)

        sample_pts: list[tuple[float, float]] = []
        for i in range(1, 11):
            t = i / 11.0
            lat = start_wp.latitude + t * (end_wp.latitude - start_wp.latitude)
            lon = start_wp.longitude + t * (end_wp.longitude - start_wp.longitude)
            sample_pts.append((round(lat * 2) / 2, round(lon * 2) / 2))

        from src.core.grib_parser import fetch_currents_batch
        currents = await fetch_currents_batch(sample_pts)
        for (lat, lon), vec in zip(sample_pts, currents):
            current_data[(lat, lon)] = (vec.u_ms, vec.v_ms)

        # Weather penalty from wave height (cheap: reuse the current cache).
        # We don't gate the reroute on it being present — zero penalty is fine.
        from src.api.v1.routers.weather import _fetch_point_weather
        import asyncio as _asyncio
        weather_pts = await _asyncio.gather(
            *[_fetch_point_weather(lat, lon) for lat, lon in sample_pts],
            return_exceptions=True,
        )
        for (lat, lon), w in zip(sample_pts, weather_pts):
            if isinstance(w, Exception):
                continue
            wave_h = w.get("wave_height")
            wind_s = w.get("wind_speed_10m")
            penalty = 0.0
            if wave_h is not None:
                penalty += max(0.0, (float(wave_h) - 1.0) / 4.0)
            if wind_s is not None:
                penalty += max(0.0, (float(wind_s) - 30.0) / 50.0)
            weather_data[(lat, lon)] = min(1.5, penalty)
    except Exception as exc:
        logger.info("Skipping environmental sampling for reroute: %s", exc)

    strategy = CurrentAwareStrategy(base, current_data, weather_data)

    try:
        path = strategy.calculate_route(_GRAPH, start_id, end_id, vessel=vessel)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not path:
        raise HTTPException(
            status_code=404,
            detail=f"No alternative route found from {start_id} to {end_id}.",
        )

    new_stats = _compute_route_stats(path, vessel)
    new_waypoints = [
        {
            "sequence": idx,
            "coordinates": [wp.longitude, wp.latitude],
            "point_type": "port" if idx in (0, len(path) - 1) else "waypoint",
            "name": wp.name or None,
        }
        for idx, wp in enumerate(path)
    ]

    original = request.current_stats or {}
    orig_dist = float(original.get("total_distance_nm") or 0.0)
    orig_dur  = float(original.get("estimated_duration_h") or 0.0)
    orig_fuel = float(original.get("estimated_fuel_tons") or 0.0)

    deltas = {
        "distance_nm": round(new_stats["total_distance_nm"] - orig_dist, 2),
        "duration_h":  round(new_stats["estimated_duration_h"] - orig_dur, 2),
        "fuel_tons":   round(new_stats["estimated_fuel_tons"] - orig_fuel, 2),
    }

    fuel_pct = (deltas["fuel_tons"] / orig_fuel) if orig_fuel > 0 else 0.0
    time_pct = (deltas["duration_h"] / orig_dur) if orig_dur > 0 else 0.0

    # Suggest the reroute if either fuel or time improves > 1%, or if the
    # waypoint sequence differs and there's any net improvement.
    improved = (fuel_pct < -0.01) or (time_pct < -0.01)
    status = "suggested" if improved else "evaluated"

    if status == "suggested":
        bits = ["Reroute recommended"]
        if request.reason:
            bits.append(f" due to {request.reason.replace('_', ' ')}")
        bits.append(". ")
        if deltas["fuel_tons"] < 0:
            bits.append(f"Saves ~{abs(deltas['fuel_tons']):.2f} t fuel ({abs(fuel_pct*100):.1f}%). ")
        if deltas["duration_h"] < 0:
            bits.append(f"Saves ~{abs(deltas['duration_h']):.2f} h ({abs(time_pct*100):.1f}%). ")
        if deltas["distance_nm"] < 0:
            bits.append(f"Shorter by {abs(deltas['distance_nm']):.1f} NM. ")
        recommendation = "".join(bits).strip()
    else:
        recommendation = (
            "Current route remains optimal. Alternative differs by "
            f"{abs(fuel_pct*100):.1f}% fuel and {abs(time_pct*100):.1f}% time — "
            "below reroute thresholds."
        )

    return {
        "reroute_id": None,
        "status": status,
        "reason": request.reason or "manual_evaluation",
        "original_route": {
            "total_distance_nm": orig_dist,
            "estimated_duration_h": orig_dur,
            "estimated_fuel_tons": orig_fuel,
        },
        "new_route": new_stats,
        "new_waypoints": new_waypoints,
        "deltas": deltas,
        "recommendation": recommendation,
        "samples": {
            "current_points": len(current_data),
            "weather_points": len(weather_data),
        },
    }



@router.post("/reroute/apply")
def apply_reroute(request: ApplyRerouteRequest):
    """
    Persist an accepted reroute alternative as the active Route.

    Replaces the existing Route's waypoints + summary stats. Returns the
    updated route document.
    """
    service = _get_ai_service()

    try:
        result = service.apply_reroute(
            route_id=request.route_id,
            new_waypoints=request.new_waypoints,
            new_stats=request.new_stats,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Apply-reroute error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    if result is None:
        raise HTTPException(status_code=404, detail="Route not found")
    return result



@router.post("/recommendations/generate", response_model=List[RecommendationOut])
def generate_recommendations(
    request: GenerateRecommendationsRequest,
    user: User = Depends(get_current_user),
):
    require_company_access(user, request.company_id)
    """
    Run all recommendation generators for *vessel_id* or *company_id*
    and persist them. Returns the newly-created list.
    """
    service = _get_ai_service()

    try:
        results = service.generate_recommendations(
            vessel_id=request.vessel_id,
            company_id=request.company_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Generate-recommendations error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return results



@router.get("/recommendations", response_model=List[RecommendationOut])
def get_recommendations(
    vessel_id: Optional[str] = Query(default=None),
    company_id: Optional[str] = Query(default=None),
    types: Optional[str] = Query(default=None, description="Comma-separated recommendation types"),
    priority: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default="active"),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """
    Get AI recommendations, optionally filtered by vessel, company, type,
    priority, and status.  Results are sorted by priority and confidence.
    """
    require_company_access(user, company_id)
    service = _get_ai_service()

    type_list = None
    if types:
        type_list = [t.strip() for t in types.split(",")]

    try:
        results = service.get_recommendations(
            vessel_id=vessel_id,
            company_id=company_id,
            types=type_list,
            priority=priority,
            status=status,
            limit=limit,
        )
    except Exception as exc:
        logger.error("Recommendations error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return results



@router.get("/anomalies", response_model=List[AnomalyOut])
def get_anomalies(
    vessel_id: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
):
    """List detected anomalies, filterable by vessel and severity."""
    service = _get_ai_service()

    try:
        results = service.get_anomalies(vessel_id=vessel_id, severity=severity)
    except Exception as exc:
        logger.error("Anomalies error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return results



@router.post("/anomalies/scan/{vessel_id}", response_model=List[AnomalyOut])
def scan_anomalies(vessel_id: str):
    """Manually trigger an anomaly scan for a specific vessel."""
    service = _get_ai_service()

    try:
        results = service.run_anomaly_scan(vessel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Anomaly scan error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return results



@router.get("/eta/{vessel_id}/{route_id}", response_model=ETAPredictionOut)
def get_eta_prediction(vessel_id: str, route_id: str):
    """Get predictive ETA for a specific vessel on a route."""
    service = _get_ai_service()

    try:
        result = service.predict_eta(vessel_id, route_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("ETA prediction error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return result



@router.patch("/recommendations/{rec_id}", response_model=RecommendationOut)
def update_recommendation(rec_id: str, body: RecommendationUpdate):
    """Update a recommendation's status (e.g., accept or dismiss)."""
    service = _get_ai_service()

    try:
        result = service.update_recommendation(rec_id, body.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Update recommendation error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return result



async def ws_notifications(websocket: WebSocket):
    """
    WebSocket endpoint for real-time AI event notifications.

    Clients can optionally subscribe to a specific vessel or company by
    passing query parameters: ?vessel_id=xxx&company_id=yyy
    """
    vessel_id = websocket.query_params.get("vessel_id")
    company_id = websocket.query_params.get("company_id")

    await ws_manager.connect(websocket, vessel_id=vessel_id, company_id=company_id)

    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({
                "event_type": "ack",
                "payload": {"message": data},
            })
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected (vessel=%s)", vessel_id)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        await ws_manager.disconnect(websocket)
