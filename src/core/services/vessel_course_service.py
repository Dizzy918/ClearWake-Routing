"""Assigning and changing a vessel's course.

The operations desk needs to be able to say "this ship is now going to
Rotterdam" and have the route computed, stored against the vessel, recorded
for audit, and shown on every dashboard watching that fleet.

Plotting starts from where the ship actually is, not from its last declared
port: a vessel already at sea should be routed from its current position.
"""

from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId

from src.core.events.dispatcher import dispatcher
from src.core.events.event import Event
from src.core.graph_builder import build_navigation_graph
from src.core.ports import resolve_port
from src.core.routing.strategy import (
    DEFAULT_FUEL_RATE,
    DEFAULT_SPEED_KNOTS,
    EcoStrategy,
    FastestStrategy,
    VesselConstraints,
)
from src.core.time_utils import utc_now
from src.models.route import Route
from src.models.vessel import Vessel

logger = logging.getLogger(__name__)

METRES_PER_NM = 1852.0


class CourseError(ValueError):
    """The requested course cannot be plotted."""


def _strategy_for(name: str):
    return EcoStrategy() if name == "eco" else FastestStrategy()


def _constraints_from(vessel: Vessel) -> VesselConstraints:
    specs = vessel.specs
    return VesselConstraints(
        vessel_type=vessel.vessel_type,
        max_draft_m=getattr(specs, "max_draft_m", None) if specs else None,
        max_speed_knots=(getattr(specs, "max_speed_knots", None) if specs else None)
        or DEFAULT_SPEED_KNOTS,
        fuel_consumption_rate=vessel.fuel_consumption_rate or DEFAULT_FUEL_RATE,
        length_m=getattr(specs, "length_m", None) if specs else None,
        beam_m=getattr(specs, "beam_m", None) if specs else None,
    )


class VesselCourseService:
    def _nearest_node(self, graph, lon: float, lat: float) -> Optional[str]:
        """Graph node closest to a position, by great-circle distance."""
        from src.core.spatial.geometry import great_circle_km

        best_id, best_km = None, float("inf")
        for node_id, node in getattr(graph, "_nodes", {}).items():
            km = great_circle_km((lon, lat), (node.longitude, node.latitude))
            if km < best_km:
                best_id, best_km = node_id, km
        return best_id

    def _origin_node(self, graph, vessel: Vessel) -> str:
        """Where to plot from: the vessel's own position when it has one."""
        if vessel.current_position:
            lon, lat = vessel.current_position["coordinates"]
            node = self._nearest_node(graph, lon, lat)
            if node:
                return node
        raise CourseError(
            f"{vessel.name} has no known position; report a position before setting a course"
        )

    def set_course(
        self,
        vessel: Vessel,
        destination_port: str,
        strategy: str,
        actor,
        reason: Optional[str] = None,
    ) -> dict:
        graph = build_navigation_graph()

        destination = resolve_port(destination_port)
        if destination is None:
            raise CourseError(f"unknown destination port: {destination_port}")
        destination_id = getattr(destination, "port_id", None) or str(destination)

        if destination_id not in getattr(graph, "_nodes", {}):
            raise CourseError(f"{destination_id} is not reachable on the routing graph")

        origin_id = self._origin_node(graph, vessel)
        if origin_id == destination_id:
            raise CourseError(f"{vessel.name} is already at {destination_id}")

        path = _strategy_for(strategy).calculate_route(
            graph, origin_id, destination_id, _constraints_from(vessel)
        )
        if not path:
            raise CourseError(
                f"no route from {origin_id} to {destination_id} that respects "
                f"{vessel.name}'s constraints and the active zones"
            )

        waypoints = [
            {"node_id": w.node_id, "lon": w.longitude, "lat": w.latitude} for w in path
        ]
        distance_nm = self._distance_nm(path)
        speed = _constraints_from(vessel).max_speed_knots or DEFAULT_SPEED_KNOTS
        duration_h = distance_nm / speed if speed else None

        route = Route(
            request_id=ObjectId(),
            company_id=vessel.company_id,
            vessel_id=vessel.id,
            optimization_mode=strategy,
            total_distance_nm=round(distance_nm, 2),
            estimated_duration_h=round(duration_h, 2) if duration_h else None,
            estimated_fuel_tons=round(vessel.calculate_fuel(distance_nm), 2),
            waypoints=[
                {
                    "sequence": index,
                    "coordinates": [w["lon"], w["lat"]],
                    "point_type": "port"
                    if w["node_id"] in (origin_id, destination_id)
                    else "waypoint",
                    "name": w["node_id"] if not w["node_id"].startswith("WP_") else None,
                }
                for index, w in enumerate(waypoints)
            ],
        )
        route.save()

        previous_route_id = vessel.active_route_id
        vessel.active_route_id = route.id
        vessel.destination_port = destination_id
        vessel.current_status = "en_route"
        vessel.save()

        payload = {
            "company_id": str(vessel.company_id),
            "vessel_id": str(vessel.id),
            "vessel_name": vessel.name,
            "route_id": str(route.id),
            "previous_route_id": str(previous_route_id) if previous_route_id else None,
            "origin": origin_id,
            "destination": destination_id,
            "strategy": strategy,
            "distance_nm": round(distance_nm, 2),
            "waypoints": waypoints,
            "changed_by": getattr(actor, "email", None),
            "reason": reason,
        }
        # Audit trail and live update both hang off the same event.
        dispatcher.dispatch(Event("vessel_course_changed", payload))

        return payload

    def clear_course(self, vessel: Vessel, actor) -> dict:
        vessel.active_route_id = None
        vessel.destination_port = None
        vessel.current_status = "idle"
        vessel.save()

        payload = {
            "company_id": str(vessel.company_id),
            "vessel_id": str(vessel.id),
            "vessel_name": vessel.name,
            "changed_by": getattr(actor, "email", None),
        }
        dispatcher.dispatch(Event("vessel_course_cleared", payload))
        return payload

    @staticmethod
    def _distance_nm(path) -> float:
        from src.core.spatial.geometry import great_circle_km

        total_km = sum(
            great_circle_km((a.longitude, a.latitude), (b.longitude, b.latitude))
            for a, b in zip(path, path[1:])
        )
        return total_km * 1000.0 / METRES_PER_NM
