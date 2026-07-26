"""Re-plan voyages when the sea changes under them.

When a canal closes or a zone is activated, the routes already sailing through
it become invalid. This finds them, works out a new course from where each
vessel actually is, and tells the operator — rather than waiting for someone
to notice the ship is heading for a closed strait.

Deliberately conservative:

* Only vessels that are *en route* with an active route are considered. An
  idle ship at a berth needs no attention.
* The new course is plotted from the vessel's last known position, so it does
  not route back through water already sailed.
* If no legal alternative exists, that is reported as an alert rather than
  quietly leaving the old route in place — a human has to make that call.
"""

from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId

from src.core.events.dispatcher import dispatcher
from src.core.events.event import Event
from src.core.graph_builder import build_navigation_graph
from src.core.spatial.geometry import (
    bbox_of_geometry,
    bboxes_overlap,
    coarse_segment_bbox,
    densify_geodesic,
    polyline_bbox,
    polyline_intersects_geometry,
)
from src.models.route import Route
from src.models.vessel import Vessel

logger = logging.getLogger(__name__)


class AutoRerouteService:
    """Finds and re-plans voyages affected by a zone becoming active."""

    def __init__(self, course_service=None):
        # Imported lazily: the course service imports routing, which is heavy.
        if course_service is None:
            from src.core.services.vessel_course_service import VesselCourseService

            course_service = VesselCourseService()
        self._course_service = course_service

    # -- finding the affected ------------------------------------------------

    def routes_crossing(self, geometry: dict) -> list[tuple[Vessel, Route]]:
        """Active voyages whose planned track passes through *geometry*."""
        if not geometry:
            return []

        zone_box = bbox_of_geometry(geometry)
        affected: list[tuple[Vessel, Route]] = []

        for vessel in Vessel.objects(current_status="en_route", active_route_id__ne=None):
            route = Route.objects(id=vessel.active_route_id).first()
            if not route or not route.waypoints:
                continue
            if self._route_hits(route, geometry, zone_box):
                affected.append((vessel, route))
        return affected

    @staticmethod
    def _route_hits(route: Route, geometry: dict, zone_box) -> bool:
        points = [wp.coordinates for wp in route.waypoints if wp.coordinates]
        for a, b in zip(points, points[1:]):
            if not bboxes_overlap(coarse_segment_bbox(a, b), zone_box):
                continue
            arc = densify_geodesic(a, b)
            if not bboxes_overlap(polyline_bbox(arc), zone_box):
                continue
            if polyline_intersects_geometry(arc, geometry):
                return True
        return False

    # -- acting on it --------------------------------------------------------

    def handle_zone_activated(self, zone) -> dict:
        """Re-plan every voyage the newly active zone blocks."""
        geometry = self._geometry_of(zone)
        affected = self.routes_crossing(geometry)

        rerouted, failed = [], []
        for vessel, old_route in affected:
            destination = vessel.destination_port or old_route.waypoints[-1].name
            if not destination:
                failed.append(
                    {"vessel_id": str(vessel.id), "vessel_name": vessel.name,
                     "reason": "no recorded destination"}
                )
                continue
            try:
                # Eco keeps clear of active zones, which is the whole point here.
                result = self._course_service.set_course(
                    vessel=vessel,
                    destination_port=destination,
                    strategy="eco",
                    actor=_SystemActor(),
                    reason=f"automatic reroute: {getattr(zone, 'name', 'a zone')} closed",
                )
                rerouted.append(
                    {
                        "vessel_id": str(vessel.id),
                        "vessel_name": vessel.name,
                        "destination": destination,
                        "new_route_id": result["route_id"],
                        "distance_nm": result["distance_nm"],
                    }
                )
            except Exception as exc:
                # No legal route left, or the destination is now unreachable.
                logger.warning("could not reroute %s: %s", vessel.name, exc)
                failed.append(
                    {"vessel_id": str(vessel.id), "vessel_name": vessel.name, "reason": str(exc)}
                )

        summary = {
            "zone_id": str(getattr(zone, "id", "")),
            "zone_name": getattr(zone, "name", None),
            "affected": len(affected),
            "rerouted": rerouted,
            "failed": failed,
        }

        if affected:
            # Alert each operator about their own ships only.
            for company_id in {str(v.company_id) for v, _ in affected}:
                dispatcher.broadcast_to_company(
                    company_id,
                    {
                        "event_type": "auto_reroute",
                        "payload": {
                            **summary,
                            "rerouted": [
                                r for r in rerouted
                                if self._belongs(r["vessel_id"], company_id)
                            ],
                            "failed": [
                                f for f in failed
                                if self._belongs(f["vessel_id"], company_id)
                            ],
                        },
                    },
                )
            dispatcher.dispatch(Event("routes_rerouted", summary))

        logger.info(
            "zone %s activated: %d affected, %d rerouted, %d failed",
            summary["zone_name"], summary["affected"], len(rerouted), len(failed),
        )
        return summary

    @staticmethod
    def _belongs(vessel_id: str, company_id: str) -> bool:
        vessel = Vessel.objects(id=ObjectId(vessel_id)).first()
        return bool(vessel and str(vessel.company_id) == company_id)

    @staticmethod
    def _geometry_of(zone) -> dict:
        geometry = getattr(zone, "geometry", None)
        if geometry is None:
            return {}
        if isinstance(geometry, dict):
            return geometry
        return {
            "type": getattr(geometry, "type", None),
            "coordinates": getattr(geometry, "coordinates", None),
        }


class _SystemActor:
    """Stands in for a user when the system itself changes a course."""

    email = "system:auto-reroute"
    role = "admin"
