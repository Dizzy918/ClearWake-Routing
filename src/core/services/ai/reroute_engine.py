"""
Intelligent rerouting engine.

Evaluates whether an alternative route would provide meaningful savings
in fuel, time, or safety compared to the vessel's current route, and
produces an actionable reroute recommendation.
"""

import logging
import math
from datetime import datetime
from typing import Optional

from src.core.config import settings
from src.core.graph_builder import build_navigation_graph
from src.core.time_utils import utc_now
from src.core.routing.strategy import (
    DEFAULT_FUEL_RATE,
    DEFAULT_SPEED_KNOTS,
    EcoStrategy,
    FastestStrategy,
    METRES_PER_NM,
    VesselConstraints,
)

logger = logging.getLogger(__name__)


class RerouteEngine:
    """
    Compares a vessel's active route against the best alternative the
    navigation graph can produce, and recommends a reroute when the
    savings exceed configurable thresholds.
    """

    def __init__(
        self,
        ai_repo,
        route_repo,
        vessel_repo,
        graph=None,
    ) -> None:
        self._ai_repo = ai_repo
        self._route_repo = route_repo
        self._vessel_repo = vessel_repo
        self._graph = graph or build_navigation_graph()

    def evaluate_reroute(
        self,
        vessel_id: str,
        reason: Optional[str] = None,
        current_position: Optional[list] = None,
        force: bool = False,
    ) -> dict:
        """
        Evaluate whether a reroute is beneficial for *vessel_id*.

        Parameters
        ----------
        vessel_id:
            The vessel to evaluate.
        reason:
            Optional context string (e.g. ``"zone_closed"``, ``"storm"``).
        current_position:
            ``[longitude, latitude]`` override — used when the vessel's
            DB position is stale.
        force:
            If ``True`` the reroute is always marked as *suggested*
            regardless of delta thresholds.

        Returns
        -------
        dict with reroute details including original vs. new route
        statistics and a human-readable recommendation.
        """
        vessel = self._vessel_repo.get_by_id(vessel_id)
        if vessel is None:
            return {"status": "error", "message": f"Vessel {vessel_id} not found."}

        routes = self._route_repo.get_by_vessel(str(vessel.id))
        original_route = None
        for r in routes:
            if r.is_valid:
                original_route = r
                break

        if original_route is None:
            return {
                "status": "skipped",
                "message": "No active route found for the vessel.",
            }

        position = current_position
        if position is None and vessel.current_position:
            coords = vessel.current_position.get("coordinates", [])
            if len(coords) >= 2:
                position = coords  # [lon, lat]

        if position is None:
            return {
                "status": "skipped",
                "message": "Vessel position is unavailable.",
            }

        vessel_lon, vessel_lat = position[0], position[1]

        constraints = self._build_vessel_constraints(vessel)

        start_node = self._find_nearest_waypoint([vessel_lon, vessel_lat], self._graph)
        if start_node is None:
            return {
                "status": "skipped",
                "message": "Cannot locate vessel on the navigation graph.",
            }

        if not original_route.waypoints:
            return {
                "status": "skipped",
                "message": "Original route has no waypoints.",
            }

        last_wp = original_route.waypoints[-1]
        if not last_wp.coordinates or len(last_wp.coordinates) < 2:
            return {
                "status": "skipped",
                "message": "Original route destination coordinates are missing.",
            }

        end_node = self._find_nearest_waypoint(
            [last_wp.coordinates[0], last_wp.coordinates[1]], self._graph
        )
        if end_node is None:
            return {
                "status": "skipped",
                "message": "Cannot locate destination on the navigation graph.",
            }

        strategy = (
            EcoStrategy()
            if original_route.optimization_mode == "eco"
            else FastestStrategy()
        )
        new_path = strategy.calculate_route(
            self._graph, start_node, end_node, vessel=constraints
        )

        if new_path is None:
            return {
                "status": "no_alternative",
                "message": "No alternative route could be found.",
            }

        original_stats = {
            "total_distance_nm": original_route.total_distance_nm or 0.0,
            "estimated_duration_h": original_route.estimated_duration_h or 0.0,
            "estimated_fuel_tons": original_route.estimated_fuel_tons or 0.0,
        }

        new_stats = self._compute_route_stats(new_path, constraints)
        new_waypoints = self._format_waypoints(new_path)

        dist_delta = new_stats["total_distance_nm"] - original_stats["total_distance_nm"]
        eta_delta = new_stats["estimated_duration_h"] - original_stats["estimated_duration_h"]
        fuel_delta = new_stats["estimated_fuel_tons"] - original_stats["estimated_fuel_tons"]

        # Percentage deltas (negative means savings).
        fuel_pct = (
            fuel_delta / original_stats["estimated_fuel_tons"]
            if original_stats["estimated_fuel_tons"] > 0
            else 0.0
        )
        time_pct = (
            eta_delta / original_stats["estimated_duration_h"]
            if original_stats["estimated_duration_h"] > 0
            else 0.0
        )

        urgent_reasons = {"zone_closed", "storm", "emergency", "conflict"}
        is_urgent = reason and reason.lower() in urgent_reasons

        should_reroute = (
            force
            or is_urgent
            or fuel_pct < -settings.AI_REROUTE_FUEL_THRESHOLD
            or time_pct < -settings.AI_REROUTE_TIME_THRESHOLD
        )

        status = "suggested" if should_reroute else "evaluated"

        recommendation = self._build_recommendation_text(
            status, reason, fuel_delta, eta_delta, dist_delta, fuel_pct, time_pct
        )

        reroute_data = {
            "vessel_id": vessel_id,
            "original_route_id": str(original_route.id),
            "reason": reason or "routine_evaluation",
            "status": status,
            "original_stats": original_stats,
            "new_stats": new_stats,
            "new_waypoints": new_waypoints,
            "deltas": {
                "distance_nm": round(dist_delta, 4),
                "duration_h": round(eta_delta, 4),
                "fuel_tons": round(fuel_delta, 4),
                "fuel_pct": round(fuel_pct * 100, 2),
                "time_pct": round(time_pct * 100, 2),
            },
            "recommendation": recommendation,
        }

        reroute_id = None
        try:
            saved = self._ai_repo.create_reroute_log(reroute_data)
            reroute_id = str(saved.id) if hasattr(saved, "id") else None
        except Exception:
            logger.exception("Failed to persist reroute log.")

        logger.info(
            "Reroute evaluation for vessel %s: %s (fuel Δ %.1f%%, time Δ %.1f%%)",
            vessel_id,
            status,
            fuel_pct * 100,
            time_pct * 100,
        )

        return {
            "reroute_id": reroute_id,
            "vessel_id": vessel_id,
            "status": status,
            "original_route": original_stats,
            "new_route": new_stats,
            "new_waypoints": new_waypoints,
            "deltas": reroute_data["deltas"],
            "recommendation": recommendation,
            "reason": reason or "routine_evaluation",
            "evaluated_at": utc_now().isoformat(),
        }

    def _find_nearest_waypoint(self, position: list, graph) -> Optional[str]:
        """
        Return the ``node_id`` of the graph waypoint nearest to
        *position* ``[lon, lat]``.
        """
        if not position or len(position) < 2:
            return None

        lon, lat = position[0], position[1]
        best_id: Optional[str] = None
        best_dist = float("inf")

        for wp in graph.get_all_waypoints():
            d = self._haversine_distance(lat, lon, wp.latitude, wp.longitude)
            if d < best_dist:
                best_dist = d
                best_id = wp.node_id

        return best_id

    def _compute_route_stats(
        self, waypoints, vessel_constraints: Optional[VesselConstraints] = None
    ) -> dict:
        """
        Compute total distance, estimated duration, and fuel for a list
        of graph :class:`Waypoint` objects.
        """
        total_distance_m = 0.0
        for i in range(len(waypoints) - 1):
            total_distance_m += waypoints[i].distance_to(waypoints[i + 1])

        total_distance_nm = total_distance_m / METRES_PER_NM

        speed = DEFAULT_SPEED_KNOTS
        if vessel_constraints and vessel_constraints.max_speed_knots:
            speed = vessel_constraints.max_speed_knots

        duration_h = total_distance_nm / speed if speed > 0 else 0.0

        fuel_rate = DEFAULT_FUEL_RATE
        fuel_multiplier = 1.0
        if vessel_constraints:
            if vessel_constraints.fuel_consumption_rate:
                fuel_rate = vessel_constraints.fuel_consumption_rate
            fuel_multiplier = vessel_constraints.fuel_multiplier

        fuel_tons = fuel_rate * total_distance_nm * fuel_multiplier

        return {
            "total_distance_nm": round(total_distance_nm, 4),
            "estimated_duration_h": round(duration_h, 4),
            "estimated_fuel_tons": round(fuel_tons, 4),
        }

    @staticmethod
    def _build_vessel_constraints(vessel) -> VesselConstraints:
        """Build a :class:`VesselConstraints` from a Vessel document."""
        specs = vessel.specs
        return VesselConstraints(
            vessel_type=vessel.vessel_type,
            max_draft_m=specs.max_draft_m if specs else None,
            max_speed_knots=specs.max_speed_knots if specs else None,
            fuel_consumption_rate=vessel.fuel_consumption_rate,
            fuel_multiplier=1.0,
            length_m=specs.length_m if specs else None,
            beam_m=specs.beam_m if specs else None,
        )

    @staticmethod
    def _format_waypoints(path) -> list:
        """
        Convert a list of graph :class:`Waypoint` objects into a
        serialisable list of dicts compatible with the Route model.
        """
        formatted: list[dict] = []
        for idx, wp in enumerate(path):
            point_type = "waypoint"
            if idx == 0:
                point_type = "port"
            elif idx == len(path) - 1:
                point_type = "port"

            formatted.append({
                "sequence": idx,
                "coordinates": [wp.longitude, wp.latitude],
                "point_type": point_type,
                "name": wp.name or wp.node_id,
            })
        return formatted

    @staticmethod
    def _build_recommendation_text(
        status: str,
        reason: Optional[str],
        fuel_delta: float,
        eta_delta: float,
        dist_delta: float,
        fuel_pct: float,
        time_pct: float,
    ) -> str:
        """Compose a human-readable recommendation string."""
        if status != "suggested":
            return (
                "Current route remains optimal.  "
                f"Alternative differs by {abs(fuel_pct * 100):.1f}% fuel "
                f"and {abs(time_pct * 100):.1f}% time — below reroute thresholds."
            )

        parts = ["Reroute recommended"]
        if reason:
            parts.append(f" due to {reason.replace('_', ' ')}")
        parts.append(". ")

        if fuel_delta < 0:
            parts.append(f"Saves ~{abs(fuel_delta):.2f} tons of fuel ({abs(fuel_pct * 100):.1f}%). ")
        if eta_delta < 0:
            parts.append(f"Saves ~{abs(eta_delta):.2f} hours ({abs(time_pct * 100):.1f}%). ")
        if dist_delta < 0:
            parts.append(f"Shorter by {abs(dist_delta):.1f} NM. ")

        return "".join(parts).strip()

    @staticmethod
    def _haversine_distance(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Great-circle distance in nautical miles."""
        R_NM = 3440.065
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return R_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
