"""
Route and operational recommendation engine.

Generates actionable recommendations for vessels and companies based on
current route strategies, vessel speed, zone activity, and seasonal
Mediterranean weather patterns.
"""

import logging
from datetime import datetime
from typing import Optional

from src.core.config import settings
from src.core.routing.strategy import DEFAULT_SPEED_KNOTS
from src.core.time_utils import utc_now

logger = logging.getLogger(__name__)

_HIGH_SPEED_THRESHOLD = 16.0
_OPTIMAL_SPEED = 14.0
_FUEL_SAVINGS_PCT_PER_KNOT = 4.0

_SUMMER_MONTHS = {5, 6, 7, 8, 9}


class RecommendationEngine:
    """
    Generates and manages operational recommendations for vessels.

    Combines database-stored recommendations with dynamically generated
    ones based on current vessel state and environmental conditions.
    """

    def __init__(self, ai_repo, route_repo, vessel_repo) -> None:
        self._ai_repo = ai_repo
        self._route_repo = route_repo
        self._vessel_repo = vessel_repo

    def get_recommendations(
        self,
        vessel_id: Optional[str] = None,
        company_id: Optional[str] = None,
        types: Optional[list] = None,
        priority: Optional[str] = None,
        status: str = "active",
        limit: int = 20,
    ) -> list:
        """
        Fetch stored recommendations and merge with freshly generated ones.

        Parameters
        ----------
        vessel_id:   Filter by vessel.
        company_id:  Filter by company.
        types:       List of recommendation_type strings to include.
        priority:    Filter by priority level (``low``, ``medium``, ``high``).
        status:      Recommendation status (default ``active``).
        limit:       Maximum number of results.
        """
        stored: list[dict] = []
        try:
            stored = self._ai_repo.get_recommendations(
                vessel_id=vessel_id,
                company_id=company_id,
                status=status,
                limit=limit,
            )
        except Exception:
            logger.exception("Failed to fetch stored recommendations.")

        # Convert mongoengine documents to dicts if necessary.
        results: list[dict] = []
        for rec in stored:
            if hasattr(rec, "to_mongo"):
                d = rec.to_mongo().to_dict()
                d["_id"] = str(d.get("_id", ""))
                results.append(d)
            elif isinstance(rec, dict):
                results.append(rec)

        # Apply optional type / priority filters on the Python side in
        # case the repository doesn't support them natively.
        if types:
            results = [
                r for r in results if r.get("recommendation_type") in types
            ]
        if priority:
            results = [r for r in results if r.get("priority") == priority]

        return results[:limit]

    def generate_all(
        self,
        vessel_id: Optional[str] = None,
        company_id: Optional[str] = None,
    ) -> list:
        """
        Run every recommendation generator for the given scope and
        persist the results.

        Returns a list of newly created recommendation dicts.
        """
        generators = [
            self._generate_route_optimization,
            self._generate_speed_advisory,
            self._generate_zone_avoidance,
            self._generate_weather_advisory,
        ]

        new_recs: list[dict] = []

        if vessel_id:
            for gen in generators:
                rec = gen(vessel_id, company_id)
                if rec is not None:
                    new_recs.append(rec)
        elif company_id:
            # Generate for every vessel belonging to the company.
            try:
                vessels = self._vessel_repo.get_by_company(company_id)
            except Exception:
                logger.exception("Failed to load vessels for company %s.", company_id)
                vessels = []

            for vessel in vessels:
                for gen in generators:
                    rec = gen(str(vessel.id), company_id)
                    if rec is not None:
                        new_recs.append(rec)

        for rec in new_recs:
            try:
                self._ai_repo.create_recommendation(rec)
            except Exception:
                logger.exception("Failed to persist recommendation: %s", rec.get("title"))

        logger.info("Generated %d new recommendations.", len(new_recs))
        return new_recs

    def _generate_route_optimization(
        self, vessel_id: str, company_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Suggest switching optimisation mode (fastest ↔ eco) based on
        the vessel's recent routing history.
        """
        routes = self._route_repo.get_by_vessel(vessel_id)
        if not routes:
            return None

        recent = routes[0]  # Already ordered by -calculated_at.
        current_mode = recent.optimization_mode or "fastest"

        if current_mode == "fastest":
            title = "Consider switching to Eco routing"
            description = (
                "Your recent routes use the Fastest strategy.  Switching to "
                "Eco mode can reduce fuel consumption by 10–18% with a modest "
                "increase in transit time, especially on Mediterranean routes "
                "where zone-avoidance re-routing is minimal."
            )
            priority = "medium"
            confidence = 0.80
        else:
            title = "Consider switching to Fastest routing"
            description = (
                "Your recent routes use the Eco strategy.  If schedule "
                "adherence is critical, switching to Fastest mode can shave "
                "hours off transit time with a controlled fuel-cost increase."
            )
            priority = "low"
            confidence = 0.70

        return {
            "vessel_id": vessel_id,
            "company_id": company_id or str(getattr(recent, "company_id", "")),
            "recommendation_type": "route_optimization",
            "title": title,
            "description": description,
            "data": {
                "current_mode": current_mode,
                "suggested_mode": "eco" if current_mode == "fastest" else "fastest",
                "recent_route_id": str(recent.id),
            },
            "confidence": confidence,
            "priority": priority,
            "status": "active",
            "created_at": utc_now().isoformat(),
        }

    def _generate_speed_advisory(
        self, vessel_id: str, company_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        If the vessel is en-route and sailing above the high-speed
        threshold, recommend slowing down for fuel savings.
        """
        vessel = self._vessel_repo.get_by_id(vessel_id)
        if vessel is None or vessel.current_status != "en_route":
            return None

        max_speed = DEFAULT_SPEED_KNOTS
        if vessel.specs and vessel.specs.max_speed_knots:
            max_speed = vessel.specs.max_speed_knots

        if max_speed <= _HIGH_SPEED_THRESHOLD:
            return None

        reduction_knots = max_speed - _OPTIMAL_SPEED
        estimated_savings_pct = reduction_knots * _FUEL_SAVINGS_PCT_PER_KNOT

        return {
            "vessel_id": vessel_id,
            "company_id": company_id or str(vessel.company_id),
            "recommendation_type": "speed_advisory",
            "title": "Reduce speed for fuel savings",
            "description": (
                f"Current maximum service speed is {max_speed:.1f} kn.  "
                f"Reducing to {_OPTIMAL_SPEED:.1f} kn can save approximately "
                f"{estimated_savings_pct:.0f}% on fuel consumption "
                f"(~{reduction_knots:.1f} kn reduction).  This is particularly "
                "effective on longer Mediterranean crossings."
            ),
            "data": {
                "current_speed_knots": max_speed,
                "recommended_speed_knots": _OPTIMAL_SPEED,
                "estimated_fuel_savings_pct": round(estimated_savings_pct, 1),
            },
            "confidence": 0.85,
            "priority": "medium",
            "status": "active",
            "created_at": utc_now().isoformat(),
        }

    def _generate_zone_avoidance(
        self, vessel_id: str, company_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Check if the vessel's current route passes near zones that are
        (or may become) restricted, and recommend proactive avoidance.
        """
        routes = self._route_repo.get_by_vessel(vessel_id)
        if not routes:
            return None

        route = None
        for r in routes:
            if r.is_valid:
                route = r
                break

        if route is None or not route.waypoints:
            return None

        try:
            from src.infrastructure.repositories.zone_repositories import ZoneRepository
            zone_repo = ZoneRepository()
            active_zones = zone_repo.get_active()
        except Exception:
            return None

        if not active_zones:
            return None

        # Simple proximity check: do any route waypoints fall "near"
        # a restricted zone?
        affected_zones: list[str] = []
        for wp in route.waypoints:
            if not wp.coordinates or len(wp.coordinates) < 2:
                continue
            for zone in active_zones:
                if zone.zone_type == "canal":
                    continue
                # Rough centroid distance.
                try:
                    coords = zone.geometry.get("coordinates", [[]])[0]
                    if not coords:
                        continue
                    c_lat = sum(c[1] for c in coords) / len(coords)
                    c_lon = sum(c[0] for c in coords) / len(coords)
                except (AttributeError, IndexError, TypeError):
                    continue

                import math
                dlat = math.radians(wp.coordinates[1] - c_lat)
                dlon = math.radians(wp.coordinates[0] - c_lon)
                a = (
                    math.sin(dlat / 2) ** 2
                    + math.cos(math.radians(wp.coordinates[1]))
                    * math.cos(math.radians(c_lat))
                    * math.sin(dlon / 2) ** 2
                )
                dist_nm = 3440.065 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                if dist_nm < 25.0:
                    affected_zones.append(zone.name)

        if not affected_zones:
            return None

        unique_zones = list(dict.fromkeys(affected_zones))  # preserve order

        return {
            "vessel_id": vessel_id,
            "company_id": company_id or str(getattr(route, "company_id", "")),
            "recommendation_type": "zone_avoidance",
            "title": "Route passes near restricted zones",
            "description": (
                f"Your current route passes within 25 NM of the following "
                f"active restricted zone(s): {', '.join(unique_zones[:5])}.  "
                "Consider requesting an Eco reroute to avoid potential "
                "delays or compliance issues."
            ),
            "data": {
                "affected_zones": unique_zones[:10],
                "route_id": str(route.id),
            },
            "confidence": 0.75,
            "priority": "high",
            "status": "active",
            "created_at": utc_now().isoformat(),
        }

    def _generate_weather_advisory(
        self, vessel_id: str, company_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Seasonal Mediterranean weather recommendations.

        During winter months (Oct–Apr), advise reduced speed and extra
        fuel reserves.  During summer, note favourable conditions.
        """
        vessel = self._vessel_repo.get_by_id(vessel_id)
        if vessel is None:
            return None

        month = datetime.now().month
        is_summer = month in _SUMMER_MONTHS

        if is_summer:
            title = "Favourable summer conditions"
            description = (
                "Current Mediterranean conditions are calm — typical of the "
                "May–September window.  Optimal for Fastest routing with "
                "minimal weather-related delays."
            )
            priority = "low"
            confidence = 0.90
            data = {
                "season": "summer",
                "weather_factor": settings.AI_WEATHER_FACTOR_SUMMER,
                "advisory": "no_action_needed",
            }
        else:
            title = "Winter weather advisory"
            description = (
                "The Mediterranean winter (October–April) brings higher seas "
                "and stronger winds.  Consider adding a 10–15% time buffer "
                "to ETAs, carrying additional fuel reserves, and favouring "
                "Eco routing to reduce exposure to adverse conditions."
            )
            priority = "medium"
            confidence = 0.85
            data = {
                "season": "winter",
                "weather_factor": settings.AI_WEATHER_FACTOR_WINTER,
                "advisory": "reduce_speed_extra_fuel",
            }

        return {
            "vessel_id": vessel_id,
            "company_id": company_id or str(vessel.company_id),
            "recommendation_type": "weather_advisory",
            "title": title,
            "description": description,
            "data": data,
            "confidence": confidence,
            "priority": priority,
            "status": "active",
            "created_at": utc_now().isoformat(),
        }
