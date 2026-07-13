"""
Rule-based anomaly detection for vessel operations.

Checks speed deviations, course deviations, restricted-zone violations,
and fuel-consumption anomalies using configurable thresholds from
``src.core.config.settings``.
"""

import logging
import math
from datetime import datetime
from typing import Optional

from src.core.config import settings
from src.core.routing.strategy import DEFAULT_SPEED_KNOTS, METRES_PER_NM
from src.core.time_utils import utc_now

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Stateless anomaly detector that runs a battery of rule-based checks
    against a vessel's current state, its active route, and the zone map.

    Each ``_check_*`` method returns an anomaly dict or ``None``.
    """

    def __init__(
        self,
        ai_repo,
        vessel_repo,
        route_repo,
        zone_repo=None,
    ) -> None:
        self._ai_repo = ai_repo
        self._vessel_repo = vessel_repo
        self._route_repo = route_repo
        self._zone_repo = zone_repo

    def detect_anomalies(self, vessel_id: str) -> list:
        """
        Run every anomaly check for the given vessel.

        Returns a list of anomaly dicts (may be empty if all is normal).
        """
        vessel = self._vessel_repo.get_by_id(vessel_id)
        if vessel is None:
            logger.warning("Vessel %s not found — skipping anomaly scan.", vessel_id)
            return []

        routes = self._route_repo.get_by_vessel(str(vessel.id))
        route = None
        for r in routes:
            if r.is_valid:
                route = r
                break

        anomalies: list[dict] = []
        for check in (
            self._check_speed,
            self._check_course_deviation,
            self._check_fuel_anomaly,
        ):
            result = check(vessel, route)
            if result is not None:
                anomalies.append(result)

        zone_result = self._check_zone_violations(vessel)
        if zone_result is not None:
            anomalies.append(zone_result)

        logger.info(
            "Anomaly scan for vessel %s completed — %d anomalies found.",
            vessel_id,
            len(anomalies),
        )
        return anomalies

    def _check_speed(self, vessel, route) -> Optional[dict]:
        """
        Compare the vessel's inferred speed against the expected speed
        from her route / specs.

        Severity bands (multiples of the configured threshold):
          * > 3× → critical
          * > 2× → high
          * > 1.5× → medium
          * else  → low
        """
        if route is None or not vessel.current_position:
            return None

        expected_speed = DEFAULT_SPEED_KNOTS
        if vessel.specs and vessel.specs.max_speed_knots:
            expected_speed = vessel.specs.max_speed_knots

        # Estimate actual speed from route progress.  We compare the
        # vessel's distance to the first waypoint against the total route
        # distance and the elapsed time since the route was calculated.
        if not route.waypoints or route.calculated_at is None:
            return None

        vessel_coords = vessel.current_position.get("coordinates", [])
        if len(vessel_coords) < 2:
            return None

        vessel_lon, vessel_lat = vessel_coords[0], vessel_coords[1]

        first_wp = route.waypoints[0]
        if not first_wp.coordinates or len(first_wp.coordinates) < 2:
            return None

        wp_lon, wp_lat = first_wp.coordinates[0], first_wp.coordinates[1]
        dist_to_first_nm = self._haversine_distance(vessel_lat, vessel_lon, wp_lat, wp_lon)

        elapsed_h = (utc_now() - route.calculated_at).total_seconds() / 3600.0
        if elapsed_h < 0.01:
            return None  # Not enough time elapsed for a meaningful estimate.

        distance_covered_nm = max(route.total_distance_nm - dist_to_first_nm, 0.0) if route.total_distance_nm else 0.0
        actual_speed = distance_covered_nm / elapsed_h if elapsed_h > 0 else 0.0

        deviation = abs(actual_speed - expected_speed)
        threshold = settings.AI_ANOMALY_SPEED_THRESHOLD

        if deviation <= threshold:
            return None

        ratio = deviation / threshold
        if ratio > 3.0:
            severity = "critical"
        elif ratio > 2.0:
            severity = "high"
        elif ratio > 1.5:
            severity = "medium"
        else:
            severity = "low"

        return {
            "vessel_id": str(vessel.id),
            "anomaly_type": "speed_deviation",
            "severity": severity,
            "details": {
                "expected_speed_knots": round(expected_speed, 2),
                "estimated_speed_knots": round(actual_speed, 2),
                "deviation_knots": round(deviation, 2),
                "threshold_knots": threshold,
                "detected_at": utc_now().isoformat(),
            },
        }

    def _check_course_deviation(self, vessel, route) -> Optional[dict]:
        """
        Calculate bearing from the vessel to the next waypoint on its
        route and compare with the ideal route bearing.  Flag if the
        angular difference exceeds the configured threshold.
        """
        if route is None or not vessel.current_position:
            return None

        if not route.waypoints or len(route.waypoints) < 2:
            return None

        vessel_coords = vessel.current_position.get("coordinates", [])
        if len(vessel_coords) < 2:
            return None

        vessel_lon, vessel_lat = vessel_coords[0], vessel_coords[1]

        # Identify the next waypoint the vessel should be heading towards.
        # Pick the closest waypoint that is ahead (by sequence) on the route.
        next_wp = None
        min_dist = float("inf")
        for wp in route.waypoints:
            if not wp.coordinates or len(wp.coordinates) < 2:
                continue
            d = self._haversine_distance(
                vessel_lat, vessel_lon, wp.coordinates[1], wp.coordinates[0]
            )
            if d < min_dist:
                min_dist = d
                next_wp = wp

        if next_wp is None:
            return None

        # Find the waypoint *after* next_wp in the sequence to compute
        # the expected route bearing at the vessel's position.
        subsequent_wp = None
        for wp in route.waypoints:
            if wp.sequence > next_wp.sequence:
                if wp.coordinates and len(wp.coordinates) >= 2:
                    subsequent_wp = wp
                    break

        if subsequent_wp is None:
            return None

        # Bearing the vessel *should* be following.
        route_bearing = self._calculate_bearing(
            next_wp.coordinates[1],
            next_wp.coordinates[0],
            subsequent_wp.coordinates[1],
            subsequent_wp.coordinates[0],
        )

        # Bearing the vessel *is* actually on (vessel → next waypoint).
        vessel_bearing = self._calculate_bearing(
            vessel_lat,
            vessel_lon,
            next_wp.coordinates[1],
            next_wp.coordinates[0],
        )

        # Angular difference (0–180°).
        diff = abs(route_bearing - vessel_bearing)
        if diff > 180.0:
            diff = 360.0 - diff

        threshold = settings.AI_ANOMALY_COURSE_THRESHOLD
        if diff <= threshold:
            return None

        if diff > threshold * 3:
            severity = "critical"
        elif diff > threshold * 2:
            severity = "high"
        elif diff > threshold * 1.5:
            severity = "medium"
        else:
            severity = "low"

        return {
            "vessel_id": str(vessel.id),
            "anomaly_type": "course_deviation",
            "severity": severity,
            "details": {
                "route_bearing_deg": round(route_bearing, 2),
                "vessel_bearing_deg": round(vessel_bearing, 2),
                "deviation_deg": round(diff, 2),
                "threshold_deg": threshold,
                "detected_at": utc_now().isoformat(),
            },
        }

    def _check_zone_violations(self, vessel) -> Optional[dict]:
        """
        Check whether the vessel's current position falls inside any
        active restricted zone (eco, conflict, or temporary).
        """
        if not vessel.current_position:
            return None

        if self._zone_repo is None:
            return None

        vessel_coords = vessel.current_position.get("coordinates", [])
        if len(vessel_coords) < 2:
            return None

        vessel_lon, vessel_lat = vessel_coords[0], vessel_coords[1]

        try:
            active_zones = self._zone_repo.get_active()
        except Exception:
            logger.exception("Failed to fetch active zones.")
            return None

        for zone in active_zones:
            if zone.zone_type == "canal":
                continue  # Canals are transit passages, not restricted.

            if self._point_in_polygon(vessel_lat, vessel_lon, zone.geometry):
                return {
                    "vessel_id": str(vessel.id),
                    "anomaly_type": "zone_violation",
                    "severity": "critical",
                    "details": {
                        "zone_id": str(zone.id),
                        "zone_name": zone.name,
                        "zone_type": zone.zone_type,
                        "vessel_position": [vessel_lon, vessel_lat],
                        "detected_at": utc_now().isoformat(),
                    },
                }

        return None

    def _check_fuel_anomaly(self, vessel, route) -> Optional[dict]:
        """
        Compare expected fuel consumption for the distance travelled so
        far against what the vessel's type-specific rate would predict.
        Flag significant deviations.
        """
        if route is None or not vessel.current_position:
            return None

        if not route.total_distance_nm or not route.estimated_fuel_tons:
            return None

        vessel_coords = vessel.current_position.get("coordinates", [])
        if len(vessel_coords) < 2:
            return None

        vessel_lon, vessel_lat = vessel_coords[0], vessel_coords[1]

        # Estimate how far the vessel has progressed along the route.
        remaining_nm = self._remaining_distance(vessel_lat, vessel_lon, route)
        distance_covered_nm = max(route.total_distance_nm - remaining_nm, 0.0)

        if distance_covered_nm < 1.0:
            return None

        # Expected fuel for the distance covered.
        fuel_rate = vessel.fuel_consumption_rate or 0.05
        expected_fuel = fuel_rate * distance_covered_nm

        # Proportion of route fuel that *should* have been used.
        proportion = distance_covered_nm / route.total_distance_nm
        actual_fuel_estimate = route.estimated_fuel_tons * proportion

        deviation_pct = (
            abs(actual_fuel_estimate - expected_fuel) / expected_fuel * 100.0
            if expected_fuel > 0
            else 0.0
        )

        if deviation_pct < settings.AI_REROUTE_FUEL_THRESHOLD * 100:
            return None

        if deviation_pct > 30.0:
            severity = "high"
        elif deviation_pct > 15.0:
            severity = "medium"
        else:
            severity = "low"

        return {
            "vessel_id": str(vessel.id),
            "anomaly_type": "fuel_anomaly",
            "severity": severity,
            "details": {
                "expected_fuel_tons": round(expected_fuel, 4),
                "estimated_actual_fuel_tons": round(actual_fuel_estimate, 4),
                "deviation_pct": round(deviation_pct, 2),
                "distance_covered_nm": round(distance_covered_nm, 2),
                "detected_at": utc_now().isoformat(),
            },
        }

    @staticmethod
    def _calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Initial bearing (forward azimuth) in degrees from point 1 → point 2
        using the Haversine bearing formula.
        """
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_lambda = math.radians(lon2 - lon1)

        x = math.sin(d_lambda) * math.cos(phi2)
        y = (
            math.cos(phi1) * math.sin(phi2)
            - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
        )
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360.0) % 360.0

    @staticmethod
    def _haversine_distance(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Great-circle distance between two WGS-84 points in nautical miles."""
        R_NM = 3440.065  # Earth's mean radius in nautical miles.
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return R_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _point_in_polygon(lat: float, lon: float, geometry: dict) -> bool:
        """
        Ray-casting point-in-polygon test.

        *geometry* is expected to be a GeoJSON Polygon dict with
        ``{"type": "Polygon", "coordinates": [[[lon, lat], ...]]}``.
        """
        try:
            coords = geometry.get("coordinates", [[]])[0]
        except (AttributeError, IndexError):
            return False

        if len(coords) < 3:
            return False

        inside = False
        n = len(coords)
        j = n - 1
        for i in range(n):
            xi, yi = coords[i][0], coords[i][1]  # lon, lat
            xj, yj = coords[j][0], coords[j][1]

            if ((yi > lat) != (yj > lat)) and (
                lon < (xj - xi) * (lat - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i

        return inside

    def _remaining_distance(self, vessel_lat: float, vessel_lon: float, route) -> float:
        """
        Estimate remaining route distance (NM) from the vessel's
        current position along the route waypoints.
        """
        if not route.waypoints:
            return route.total_distance_nm or 0.0

        best_idx = 0
        best_dist = float("inf")
        for i, wp in enumerate(route.waypoints):
            if not wp.coordinates or len(wp.coordinates) < 2:
                continue
            d = self._haversine_distance(
                vessel_lat, vessel_lon, wp.coordinates[1], wp.coordinates[0]
            )
            if d < best_dist:
                best_dist = d
                best_idx = i

        remaining = best_dist

        for i in range(best_idx, len(route.waypoints) - 1):
            wp_a = route.waypoints[i]
            wp_b = route.waypoints[i + 1]
            if (
                wp_a.coordinates
                and wp_b.coordinates
                and len(wp_a.coordinates) >= 2
                and len(wp_b.coordinates) >= 2
            ):
                remaining += self._haversine_distance(
                    wp_a.coordinates[1],
                    wp_a.coordinates[0],
                    wp_b.coordinates[1],
                    wp_b.coordinates[0],
                )

        return remaining
