import copy
import json
import logging
from collections import OrderedDict
from typing import Optional
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_dependencies import get_current_user, require_company_access
from src.models.user import User
from concurrent.futures import ThreadPoolExecutor
from fastapi.responses import FileResponse

from src.schemas.route import RouteCalculationSchema
from src.core.routing.strategy import (
    FastestStrategy,
    EcoStrategy,
    VesselConstraints,
    DEFAULT_SPEED_KNOTS,
    DEFAULT_FUEL_RATE,
    METRES_PER_NM,
)
from src.infrastructure.repositories.route_repository import RouteRepository
from src.infrastructure.repositories.route_history_repository import RouteHistoryRepository
from src.core.graph_builder import build_navigation_graph
from src.core.ports import resolve_port, list_all_ports
from src.core.services.draft_trim_optimizer import (
    DraftTrimInput,
    optimize as optimize_draft_trim,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/routes", tags=["routes"], dependencies=[Depends(get_current_user)])
repo = RouteRepository()
history_repo = RouteHistoryRepository()



_FUEL_MULTIPLIERS: dict[str, float] = {
    "tanker": 1.20,
    "container_ship": 1.10,
    "bulk_carrier": 1.15,
    "passenger_ship": 1.18,
    "ferry": 1.12,
    "ro_ro_ship": 1.14,
    "lng_carrier": 1.25,
    "lpg_carrier": 1.22,
    "chemical_tanker": 1.23,
    "car_carrier": 1.14,
    "general_cargo": 1.13,
    "offshore_support": 1.30,
    "research_vessel": 1.20,
    "icebreaker": 1.40,
    "tugboat": 1.35,
    "fishing_vessel": 1.16,
    "cruise_ship": 1.17,
    "yacht": 1.08,
    "patrol_boat": 1.28,
    "dredger": 1.32,
}


_GRAPH = build_navigation_graph()
_LAND_MASK_PATH = Path(__file__).resolve().parents[4] / "ne_50m_land.geojson"
_LAND_MASK_CACHE: Optional[bytes] = None

# Bounded LRU cache for /calculate. Keyed on the inputs that actually
# determine the computed path + stats; company_id is intentionally not
# in the key so identical requests from different tenants reuse the
# expensive graph traversal.
_ROUTE_CACHE_MAX = 256
_ROUTE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()


def _route_cache_key(request: RouteCalculationSchema) -> tuple:
    return (
        request.start_node_id,
        request.end_node_id,
        request.vessel_id or "",
        request.vessel_type or "",
        request.optimization_mode,
    )


def _route_cache_get(key: tuple) -> Optional[dict]:
    if key not in _ROUTE_CACHE:
        return None
    _ROUTE_CACHE.move_to_end(key)
    return _ROUTE_CACHE[key]


def _route_cache_set(key: tuple, value: dict) -> None:
    _ROUTE_CACHE[key] = value
    _ROUTE_CACHE.move_to_end(key)
    while len(_ROUTE_CACHE) > _ROUTE_CACHE_MAX:
        _ROUTE_CACHE.popitem(last=False)




def _resolve_node_id(raw: str) -> str:
    """Resolve a user-supplied port/city name to a graph node_id."""

    if _GRAPH.has_waypoint(raw):
        return raw


    upper = raw.upper().replace(" ", "_")
    if _GRAPH.has_waypoint(upper):
        return upper


    port = resolve_port(raw)
    if port and _GRAPH.has_waypoint(port.port_id):
        return port.port_id

    raise HTTPException(
        status_code=404,
        detail=f"Port '{raw}' not found.  Use /api/v1/routes/ports to list available ports.",
    )


def _build_vessel_constraints(
    vessel_id: str = "",
    vessel_type: Optional[str] = None,
) -> Optional[VesselConstraints]:
    """
    Build VesselConstraints from either a DB vessel or a bare vessel_type.

    Returns None if no vessel info is available (route will use defaults).
    """

    if vessel_id and ObjectId.is_valid(vessel_id):
        try:
            from src.infrastructure.repositories.vessel_repository import VesselRepository
            vessel_repo = VesselRepository()
            vessel_doc = vessel_repo.get_by_id(vessel_id)
            if vessel_doc:
                specs = vessel_doc.specs
                vt = vessel_doc.vessel_type
                return VesselConstraints(
                    vessel_type=vt,
                    max_draft_m=float(specs.max_draft_m) if specs and specs.max_draft_m else None,
                    max_speed_knots=float(specs.max_speed_knots) if specs and specs.max_speed_knots else None,
                    fuel_consumption_rate=float(vessel_doc.fuel_consumption_rate) if vessel_doc.fuel_consumption_rate else None,
                    fuel_multiplier=_FUEL_MULTIPLIERS.get(vt, 1.0) if vt else 1.0,
                    length_m=float(specs.length_m) if specs and specs.length_m else None,
                    beam_m=float(specs.beam_m) if specs and specs.beam_m else None,
                    max_cargo_t=float(specs.max_cargo_t) if specs and specs.max_cargo_t else None,
                    cargo_weight_t=float(specs.cargo_weight_t) if specs and specs.cargo_weight_t is not None else None,
                    trim_m=float(specs.trim_m) if specs and specs.trim_m is not None else None,
                    hydro_resistance_coef=float(specs.hydro_resistance_coef) if specs and specs.hydro_resistance_coef else None,
                )
        except Exception as exc:
            logger.debug("Could not load vessel %s from DB: %s", vessel_id, exc)


    if vessel_type:
        return VesselConstraints(
            vessel_type=vessel_type,
            fuel_multiplier=_FUEL_MULTIPLIERS.get(vessel_type, 1.0),
        )

    return None


def _draft_trim_savings(
    vessel: Optional[VesselConstraints],
    wave_height_m: float = 0.0,
) -> Optional[dict]:
    """
    Run the draft/trim optimizer when the vessel has enough loading data.

    Returns the optimiser result as a dict (including ``fuel_savings_pct``),
    or ``None`` when any required field is missing.
    """
    if vessel is None:
        return None
    required = (vessel.length_m, vessel.beam_m, vessel.max_draft_m,
                vessel.max_speed_knots, vessel.max_cargo_t, vessel.cargo_weight_t)
    if any(v is None for v in required):
        return None
    try:
        result = optimize_draft_trim(
            DraftTrimInput(
                length_m=vessel.length_m,
                beam_m=vessel.beam_m,
                max_draft_m=vessel.max_draft_m,
                speed_knots=vessel.max_speed_knots,
                cargo_weight_t=vessel.cargo_weight_t,
                max_cargo_t=vessel.max_cargo_t,
                wave_height_m=wave_height_m,
            )
        )
    except ValueError:
        return None
    return result.as_dict()


def _compute_route_stats(
    waypoints,
    vessel: Optional[VesselConstraints] = None,
    wave_height_m: float = 0.0,
) -> dict:
    """
    Compute total distance (NM), duration (hours), and fuel (tonnes)
    from an ordered list of graph Waypoint objects.

    When the vessel has cargo + dimensions, the draft/trim optimiser is run
    and its fuel savings reduce ``estimated_fuel_tons``. The hull
    ``hydro_resistance_coef`` (if set) multiplies the base fuel burn.
    """
    total_metres = 0.0
    for i in range(len(waypoints) - 1):
        total_metres += waypoints[i].distance_to(waypoints[i + 1])

    total_nm = total_metres / METRES_PER_NM

    speed = DEFAULT_SPEED_KNOTS
    fuel_rate = DEFAULT_FUEL_RATE
    fuel_mult = 1.0
    hydro_mult = 1.0

    if vessel:
        if vessel.max_speed_knots:
            speed = vessel.max_speed_knots
        if vessel.fuel_consumption_rate:
            fuel_rate = vessel.fuel_consumption_rate
        fuel_mult = vessel.fuel_multiplier
        if vessel.hydro_resistance_coef:
            hydro_mult = vessel.hydro_resistance_coef

    duration_h = total_nm / speed if speed > 0 else 0.0
    base_fuel_tons = fuel_rate * total_nm * fuel_mult * hydro_mult

    optimization = _draft_trim_savings(vessel, wave_height_m=wave_height_m)
    if optimization:
        savings_pct = max(0.0, min(15.0, float(optimization.get("fuel_savings_pct", 0.0))))
        fuel_tons = base_fuel_tons * (1.0 - savings_pct / 100.0)
    else:
        fuel_tons = base_fuel_tons

    stats = {
        "total_distance_nm": round(total_nm, 2),
        "estimated_duration_h": round(duration_h, 2),
        "estimated_fuel_tons": round(fuel_tons, 2),
        "baseline_fuel_tons": round(base_fuel_tons, 2),
    }
    if optimization:
        stats["draft_trim_optimization"] = optimization
    return stats




@router.get("/ports")
def get_available_ports():
    """Return all ports that can be used as origin/destination."""
    ports = list_all_ports()
    return [
        {"port_id": p.port_id, "name": p.name, "lat": p.latitude, "lon": p.longitude}
        for p in ports
    ]


@router.get("/landmask")
def get_landmask_geojson():
    global _LAND_MASK_CACHE
    if _LAND_MASK_CACHE is None:
        if not _LAND_MASK_PATH.exists():
            raise HTTPException(status_code=404, detail="Land mask file is missing")
        _LAND_MASK_CACHE = _LAND_MASK_PATH.read_bytes()
    from fastapi.responses import Response
    return Response(
        content=_LAND_MASK_CACHE,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/calculate")
def calculate_route(request: RouteCalculationSchema, user: User = Depends(get_current_user)):
    require_company_access(user, request.company_id or None)

    start_id = _resolve_node_id(request.start_node_id)
    end_id = _resolve_node_id(request.end_node_id)


    vessel = _build_vessel_constraints(request.vessel_id, request.vessel_type)


    if request.optimization_mode == "eco":
        strategy = EcoStrategy()
    elif request.optimization_mode == "fastest":
        strategy = FastestStrategy()
    else:
        raise HTTPException(status_code=400, detail="Invalid optimization mode. Use 'fastest' or 'eco'.")

    cache_key = _route_cache_key(request)
    cached = _route_cache_get(cache_key)
    cache_hit = cached is not None

    if cache_hit:
        waypoints = copy.deepcopy(cached["waypoints"])
        stats = dict(cached["stats"])
    else:
        try:
            path = strategy.calculate_route(_GRAPH, start_id, end_id, vessel=vessel)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

        if not path:
            raise HTTPException(status_code=404, detail=f"No route found from {start_id} to {end_id}.")


        waypoints = []
        for idx, wp in enumerate(path):
            point_type = "waypoint"
            if idx == 0:
                point_type = "port"
            elif idx == len(path) - 1:
                point_type = "port"
            elif not wp.node_id.startswith("WP_"):
                point_type = "port"

            waypoints.append({
                "sequence": idx,
                "coordinates": [wp.longitude, wp.latitude],
                "point_type": point_type,
                "name": wp.name if not wp.node_id.startswith("WP_") else None,
            })


        stats = _compute_route_stats(path, vessel)
        _route_cache_set(cache_key, {"waypoints": copy.deepcopy(waypoints), "stats": dict(stats)})


    route_data = {
        "request_id": ObjectId(),
        "company_id": ObjectId(request.company_id) if ObjectId.is_valid(request.company_id) else user.company_id,
        "vessel_id": ObjectId(request.vessel_id) if ObjectId.is_valid(request.vessel_id) else ObjectId(),
        "optimization_mode": request.optimization_mode,
        "total_distance_nm": stats["total_distance_nm"],
        "estimated_duration_h": stats["estimated_duration_h"],
        "estimated_fuel_tons": stats["estimated_fuel_tons"],
        "waypoints": waypoints,
    }

    try:
        created_route = repo.create(route_data)
        result = json.loads(created_route.to_json())

        try:
            history_data = {
                "route_id": created_route.id,
                "vessel_id": route_data["vessel_id"],
                "company_id": route_data["company_id"],
                "origin_port": start_id,
                "destination_port": end_id,
                "optimization_mode": request.optimization_mode,
                "total_distance_nm": stats["total_distance_nm"],
                "estimated_duration_h": stats["estimated_duration_h"],
                "estimated_fuel_tons": stats["estimated_fuel_tons"],
                "waypoint_count": len(waypoints),
                "status": "completed",
            }
            history_repo.create(history_data)
        except Exception as hist_exc:
            logger.warning("Could not persist route history: %s", hist_exc)
    except Exception as exc:
        logger.warning("Could not persist route to DB: %s", exc)
        result = {"_id": None, "waypoints": waypoints}


    result["total_distance_nm"] = stats["total_distance_nm"]
    result["estimated_duration_h"] = stats["estimated_duration_h"]
    result["estimated_fuel_tons"] = stats["estimated_fuel_tons"]
    result["baseline_fuel_tons"] = stats.get("baseline_fuel_tons")
    if "draft_trim_optimization" in stats:
        result["draft_trim_optimization"] = stats["draft_trim_optimization"]
    result["vessel_type_used"] = vessel.vessel_type if vessel else None
    result["start_port"] = start_id
    result["end_port"] = end_id
    result["cache_hit"] = cache_hit

    return result


@router.get("/history")
def get_route_history(limit: int = 50, user: User = Depends(get_current_user)):
    """Return the caller's company's most recent route-history entries."""
    entries = history_repo.get_by_company(str(user.company_id), limit=limit)
    return [json.loads(e.to_json()) for e in entries]


@router.get("/history/{vessel_id}")
def get_route_history_by_vessel(
    vessel_id: str,
    limit: int = 50,
    user: User = Depends(get_current_user),
):
    """Return route-history entries for a specific vessel (own company only)."""
    entries = history_repo.get_by_vessel(vessel_id, limit=limit)
    entries = [e for e in entries if e.company_id == user.company_id]
    return [json.loads(e.to_json()) for e in entries]


@router.get("/{route_id}")
def get_route_by_id(route_id: str, user: User = Depends(get_current_user)):
    route = repo.get_by_id(route_id)
    if not route or route.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Route not found")
    return json.loads(route.to_json())


executor = ThreadPoolExecutor(max_workers=5)


@router.post("/calculate-batch")
def calculate_routes_batch(
    requests: list[RouteCalculationSchema],
    user: User = Depends(get_current_user),
):
    for request in requests:
        require_company_access(user, request.company_id or None)

    def calculate_single(request: RouteCalculationSchema):
        try:
            start_id = _resolve_node_id(request.start_node_id)
            end_id = _resolve_node_id(request.end_node_id)
        except HTTPException as e:
            return {"error": e.detail}

        vessel = _build_vessel_constraints(request.vessel_id, request.vessel_type)

        if request.optimization_mode == "eco":
            strategy = EcoStrategy()
        elif request.optimization_mode == "fastest":
            strategy = FastestStrategy()
        else:
            return {"error": "Invalid optimization mode"}

        try:
            path = strategy.calculate_route(_GRAPH, start_id, end_id, vessel=vessel)
        except KeyError as e:
            return {"error": str(e)}

        if not path:
            return {"error": "No route found"}

        waypoints = []
        for idx, wp in enumerate(path):
            waypoints.append({
                "sequence": idx,
                "coordinates": [wp.longitude, wp.latitude],
                "point_type": "waypoint",
            })

        stats = _compute_route_stats(path, vessel)

        return {
            "optimization_mode": request.optimization_mode,
            "waypoints": waypoints,
            **stats,
            "vessel_type_used": vessel.vessel_type if vessel else None,
        }

    results = list(executor.map(calculate_single, requests))
    return results
