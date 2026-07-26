from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Callable
from src.core.graph import NavigationGraph, Waypoint, Edge
from src.core.spatial.zone_spatial_service import (
    PrefetchedZoneSpatialService,
    ZoneSpatialService,
)


@dataclass
class VesselConstraints:
    """
    Vessel parameters that influence routing decisions.

    Attributes:
        vessel_type:          One of the VESSEL_TYPES strings (e.g. "tanker").
        max_draft_m:          Maximum draft of the vessel (metres).  Passages
                              whose max_draft_m is less than this are blocked.
        max_speed_knots:      Service speed in knots, used to estimate duration.
        fuel_consumption_rate: Base fuel consumption rate (tonnes per NM).
        fuel_multiplier:      Type-specific multiplier from the Vessel subclass.
        length_m:             Vessel length (metres) – can restrict canals.
        beam_m:               Vessel beam (metres) – can restrict canals.
        max_cargo_t:          Design max cargo (tonnes); used by trim optimiser.
        cargo_weight_t:       Current cargo (tonnes); used by trim optimiser.
        trim_m:               Current stern-trim (positive) in metres.
        hydro_resistance_coef: Hull-specific resistance multiplier (≈ 1.0).
    """
    vessel_type: Optional[str] = None
    max_draft_m: Optional[float] = None
    max_speed_knots: Optional[float] = None
    fuel_consumption_rate: Optional[float] = None
    fuel_multiplier: float = 1.0
    length_m: Optional[float] = None
    beam_m: Optional[float] = None
    max_cargo_t: Optional[float] = None
    cargo_weight_t: Optional[float] = None
    trim_m: Optional[float] = None
    hydro_resistance_coef: Optional[float] = None



DEFAULT_SPEED_KNOTS = 14.0
DEFAULT_FUEL_RATE = 0.05
METRES_PER_NM = 1852.0


class RoutingStrategy(ABC):
    @abstractmethod
    def calculate_route(
        self,
        graph: NavigationGraph,
        start_id: str,
        end_id: str,
        vessel: Optional[VesselConstraints] = None,
    ) -> Optional[List[Waypoint]]:
        pass


def _make_vessel_filter(vessel: Optional[VesselConstraints]) -> Optional[Callable[[Edge], bool]]:
    """
    Build an edge-filter callback for vessel constraint checks.

    Returns None if no vessel is given (no filtering needed).
    The filter returns True if the edge is passable, False if blocked.
    """
    if vessel is None:
        return None

    v_draft = vessel.max_draft_m
    v_length = vessel.length_m
    v_beam = vessel.beam_m

    # Fast path: vessel has no dimensional constraints at all
    if v_draft is None and v_length is None and v_beam is None:
        return None

    def _filter(edge: Edge) -> bool:
        if v_draft is not None and edge.max_draft_m is not None and v_draft > edge.max_draft_m:
            return False
        if v_length is not None and edge.max_length_m is not None and v_length > edge.max_length_m:
            return False
        if v_beam is not None and edge.max_beam_m is not None and v_beam > edge.max_beam_m:
            return False
        return True

    return _filter


class FastestStrategy(RoutingStrategy):
    """
    Find the shortest-distance route.

    If a vessel is provided, edges whose draft / length / beam restriction
    is exceeded by the vessel will be filtered out during pathfinding.

    Uses the shared graph directly (no copy) for maximum speed.
    """

    def calculate_route(
        self,
        graph: NavigationGraph,
        start_id: str,
        end_id: str,
        vessel: Optional[VesselConstraints] = None,
    ) -> Optional[List[Waypoint]]:
        edge_filter = _make_vessel_filter(vessel)
        return graph.find_path(start_id, end_id, edge_filter=edge_filter)


class EcoStrategy(RoutingStrategy):
    """
    Find a route that additionally avoids active ecological / restricted zones.

    Draft / length / beam restrictions from the vessel are also applied.

    Zone checks happen **lazily** — only edges A* actually explores are tested,
    not all ~8 600 edges in the graph. For a cross-graph route that is still
    around 2 000 checks, so they are answered from an in-memory snapshot of the
    active zones (:class:`PrefetchedZoneSpatialService`) instead of one
    ``$geoIntersects`` round-trip each. Measured together, that takes an eco
    route from ~2.4 s to a few milliseconds.

    Pass a different ``spatial_service`` to override; the database-backed
    :class:`ZoneSpatialService` gives the same answers, just slowly.
    """

    def __init__(self, spatial_service: Optional[ZoneSpatialService] = None):
        self.spatial_service = spatial_service or PrefetchedZoneSpatialService()

    def calculate_route(
        self,
        graph: NavigationGraph,
        start_id: str,
        end_id: str,
        vessel: Optional[VesselConstraints] = None,
    ) -> Optional[List[Waypoint]]:

        vessel_filter = _make_vessel_filter(vessel)

        # Cache zone-check results so repeated edge visits don't re-query
        zone_cache: dict[tuple[str, str], bool] = {}

        def eco_filter(edge: Edge) -> bool:
            # 1. Vessel dimensional constraints (cheap, no DB)
            if vessel_filter is not None and not vessel_filter(edge):
                return False

            # 2. Zone intersection check (expensive, cached)
            key = (edge.source.node_id, edge.destination.node_id)
            if key not in zone_cache:
                route_segment = [
                    [edge.source.longitude, edge.source.latitude],
                    [edge.destination.longitude, edge.destination.latitude],
                ]
                zone_cache[key] = not self.spatial_service.is_route_blocked(
                    route_segment, vessel,
                )
            return zone_cache[key]

        return graph.find_path(start_id, end_id, edge_filter=eco_filter)


class CurrentAwareStrategy(RoutingStrategy):
    """
    A routing strategy that accounts for ocean currents and (optionally)
    weather along the route.

    Wraps a base strategy (FastestStrategy or EcoStrategy) and adjusts
    edge weights using ocean current and weather data before pathfinding,
    so edges are scored by::

        Cost = DistanceWeight + WeatherPenalty - CurrentBoost

    ``current_data`` is a dict mapping ``(lat, lon)`` grid keys to
    ``(u_ms, v_ms)`` tuples.  ``weather_data`` is a dict mapping the same
    grid keys to a unit-less penalty in roughly ``[0, 1]`` (0 = calm,
    1 = severe).  Grid keys are rounded to 0.5° for lookup.
    """

    def __init__(
        self,
        base_strategy: RoutingStrategy,
        current_data: Optional[dict] = None,
        weather_data: Optional[dict] = None,
    ) -> None:
        self._base = base_strategy
        self._current_data = current_data or {}
        self._weather_data = weather_data or {}

    @staticmethod
    def _grid_key(lat: float, lon: float) -> tuple[float, float]:
        """Round to 0.5° grid for cache/lookup efficiency."""
        return (round(lat * 2) / 2, round(lon * 2) / 2)

    def _inject_environment(self, graph: NavigationGraph) -> None:
        """Annotate every edge with current + weather data at its midpoint."""
        if not self._current_data and not self._weather_data:
            return

        for node_id in list(graph._nodes.keys()):
            for edge in graph.get_edges(node_id):
                mid_lat = (edge.source.latitude + edge.destination.latitude) / 2
                mid_lon = (edge.source.longitude + edge.destination.longitude) / 2
                key = self._grid_key(mid_lat, mid_lon)

                if key in self._current_data:
                    u, v = self._current_data[key]
                    edge.current_u = u
                    edge.current_v = v
                if key in self._weather_data:
                    edge.weather_penalty = float(self._weather_data[key])

    def calculate_route(
        self,
        graph: NavigationGraph,
        start_id: str,
        end_id: str,
        vessel: Optional[VesselConstraints] = None,
    ) -> Optional[List[Waypoint]]:
        """
        Calculate a current- and weather-aware route by:
          1. Annotating graph edges with current + weather data.
          2. Asking A* to use effective_weight (the formula above).
        """
        self._inject_environment(graph)

        speed = (
            vessel.max_speed_knots
            if vessel and vessel.max_speed_knots
            else DEFAULT_SPEED_KNOTS
        )

        # The base strategy still applies its edge filters (vessel / zones);
        # we just ask the graph to score edges via effective_weight.
        if isinstance(self._base, EcoStrategy):
            vessel_filter = _make_vessel_filter(vessel)
            spatial_service = self._base.spatial_service
            zone_cache: dict[tuple[str, str], bool] = {}

            def eco_filter(edge: Edge) -> bool:
                if vessel_filter is not None and not vessel_filter(edge):
                    return False
                key = (edge.source.node_id, edge.destination.node_id)
                if key not in zone_cache:
                    segment = [
                        [edge.source.longitude, edge.source.latitude],
                        [edge.destination.longitude, edge.destination.latitude],
                    ]
                    zone_cache[key] = not spatial_service.is_route_blocked(
                        segment, vessel,
                    )
                return zone_cache[key]

            return graph.find_path(
                start_id, end_id,
                edge_filter=eco_filter,
                use_current_weights=True,
                vessel_speed_knots=speed,
            )

        edge_filter = _make_vessel_filter(vessel)
        return graph.find_path(
            start_id, end_id,
            edge_filter=edge_filter,
            use_current_weights=True,
            vessel_speed_knots=speed,
        )


class WeatherAwareStrategy(RoutingStrategy):
    """Routing that respects what the weather is doing to *this* ship.

    Conditions are sampled per maritime region rather than per edge — a
    forecast has nothing like edge resolution, and pretending otherwise would
    be false precision.

    A wind-dependent vessel is refused passage through severe weather: with no
    engine it may be unable to steer clear, so this is a hard constraint, not
    a cost. Engine-driven vessels are allowed through and charged a penalty,
    leaving the decision where it belongs — with the master.
    """

    def __init__(self, base_strategy: Optional[RoutingStrategy] = None, conditions=None):
        self.base_strategy = base_strategy or FastestStrategy()
        # conditions: [{"bbox": [w, s, e, n], "severity": "severe"}, ...]
        self._conditions = conditions or []

    def _severity_at(self, longitude: float, latitude: float) -> str:
        from src.core.weather_safety import SEVERITY_CALM

        worst = SEVERITY_CALM
        order = ["calm", "moderate", "rough", "severe"]
        for region in self._conditions:
            bbox = region.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            west, south, east, north = bbox
            if west <= longitude <= east and south <= latitude <= north:
                severity = region.get("severity", SEVERITY_CALM)
                if order.index(severity) > order.index(worst):
                    worst = severity
        return worst

    def calculate_route(
        self,
        graph: NavigationGraph,
        start_id: str,
        end_id: str,
        vessel: Optional[VesselConstraints] = None,
    ) -> Optional[List[Waypoint]]:
        from src.core.weather_safety import is_passable

        vessel_type = getattr(vessel, "vessel_type", None)
        base_filter = _make_vessel_filter(vessel)

        def weather_filter(edge: Edge) -> bool:
            if base_filter is not None and not base_filter(edge):
                return False
            # Test both ends: a leg is only as safe as its worst end.
            for node in (edge.source, edge.destination):
                severity = self._severity_at(node.longitude, node.latitude)
                if not is_passable(vessel_type, severity):
                    return False
            return True

        return graph.find_path(start_id, end_id, edge_filter=weather_filter)
