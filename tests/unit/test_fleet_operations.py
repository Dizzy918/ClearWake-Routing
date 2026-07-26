"""Tests for the B2B fleet features: weather safety, tracking, hazard import.

These cover the decisions that are easy to get quietly wrong — a sailing
vessel being routed into a thunderstorm, a bad GPS fix corrupting a track, a
hazard circle drawn the wrong size near the poles.
"""

from datetime import timedelta

import pytest

from src.core.services.hazard_import_service import (
    circle_polygon,
    classify_warning,
    parse_positions,
)
from src.core.time_utils import utc_now
from src.core.weather_safety import (
    SEVERITY_CALM,
    SEVERITY_ROUGH,
    SEVERITY_SEVERE,
    classify,
    is_passable,
    penalty_for,
)
from src.core.zone_presets import ZONE_PRESETS


class TestWeatherSafety:
    def test_thunderstorm_is_severe(self):
        assert classify(wind_speed_kmh=30, wave_height_m=1.5, weather_code=95) == SEVERITY_SEVERE

    def test_storm_force_wind_is_severe(self):
        assert classify(wind_speed_kmh=95) == SEVERITY_SEVERE

    def test_gale_is_rough_not_severe(self):
        assert classify(wind_speed_kmh=70) == SEVERITY_ROUGH

    def test_missing_data_never_closes_a_route(self):
        # An absent forecast must not act like bad weather.
        assert classify() == SEVERITY_CALM
        assert is_passable("tall_ship", classify())

    def test_engineless_vessel_is_blocked_by_severe_weather(self):
        # The point of the whole feature: no engine, no way out of a storm.
        assert not is_passable("tall_ship", SEVERITY_SEVERE)
        assert not is_passable("sailing_cargo_ship", SEVERITY_SEVERE)

    def test_engine_driven_vessel_is_penalised_not_blocked(self):
        assert is_passable("container_ship", SEVERITY_SEVERE)
        assert penalty_for("container_ship", SEVERITY_SEVERE) > 1.0

    def test_wind_assisted_ship_keeps_its_engine(self):
        # Wind-assisted cargo carries propulsion, so it is not stranded.
        assert is_passable("wind_assisted_cargo_ship", SEVERITY_SEVERE)

    def test_sailing_vessels_are_penalised_harder_when_rough(self):
        assert penalty_for("tall_ship", SEVERITY_ROUGH) > penalty_for(
            "container_ship", SEVERITY_ROUGH
        )


class TestHazardParsing:
    def test_parses_degrees_minutes_with_hemisphere(self):
        assert parse_positions("AGOSTO 12 19-23.0N 092-03.1W.") == [
            pytest.approx((-92.0517, 19.3833), abs=1e-3)
        ]

    def test_southern_and_eastern_hemispheres(self):
        (lon, lat), = parse_positions("POSITION 33-55.0S 018-25.0E.")
        assert lat < 0 and lon > 0

    def test_text_without_positions_yields_nothing(self):
        assert parse_positions("NAVAREA IV NAVIGATIONAL WARNING IN FORCE.") == []

    def test_classification_reads_the_warning(self):
        assert classify_warning("ARMED ROBBERY AGAINST SHIPS REPORTED") == "piracy"
        assert classify_warning("LIVE FIRING EXERCISE IN PROGRESS") == "military"
        assert classify_warning("DERELICT VESSEL ADRIFT") == "hazard"
        assert classify_warning("OIL SPILL REPORTED") == "environmental"

    def test_unrecognised_warning_defaults_to_hazard(self):
        assert classify_warning("SOMETHING UNUSUAL AT SEA") == "hazard"

    def test_circle_is_a_closed_ring(self):
        ring = circle_polygon(0, 0, 10)["coordinates"][0]
        assert ring[0] == ring[-1]

    def test_circle_widens_in_longitude_at_high_latitude(self):
        # A degree of longitude is shorter near the poles, so a keep-clear
        # circle must span more of them to stay circular on the ground.
        equator = circle_polygon(0, 0, 60)["coordinates"][0]
        high = circle_polygon(0, 60, 60)["coordinates"][0]
        width = lambda ring: max(p[0] for p in ring) - min(p[0] for p in ring)  # noqa: E731
        assert width(high) > width(equator) * 1.8


class TestZonePresets:
    def test_every_preset_is_a_closed_polygon_with_a_known_type(self):
        from src.models.zone import ZONE_TYPES

        ids = set()
        for preset in ZONE_PRESETS:
            assert preset["id"] not in ids, f"duplicate preset id {preset['id']}"
            ids.add(preset["id"])
            assert preset["zone_type"] in ZONE_TYPES, preset["id"]
            ring = preset["geometry"]["coordinates"][0]
            assert ring[0] == ring[-1], f"{preset['id']} ring is not closed"
            assert len(ring) >= 4

    def test_coordinates_are_in_range(self):
        for preset in ZONE_PRESETS:
            for lon, lat in preset["geometry"]["coordinates"][0]:
                assert -180 <= lon <= 180, preset["id"]
                assert -90 <= lat <= 90, preset["id"]

    def test_the_chokepoints_operators_actually_close_are_present(self):
        ids = {p["id"] for p in ZONE_PRESETS}
        assert {"suez_canal", "panama_canal", "strait_of_hormuz"} <= ids


class TestPositionPlausibility:
    """The speed test that keeps bad fixes out of the track."""

    def _service(self):
        from src.core.services.vessel_tracking_service import VesselTrackingService

        return VesselTrackingService()

    def _vessel(self, lon, lat, when):
        class _V:
            name = "Test Vessel"
            current_position = {"type": "Point", "coordinates": [lon, lat]}
            position_updated_at = when

        return _V()

    def test_a_realistic_passage_is_accepted(self):
        from src.core.services.vessel_tracking_service import ImplausibleJump

        now = utc_now()
        vessel = self._vessel(0.0, 50.0, now - timedelta(hours=1))
        try:
            self._service()._check_plausible(vessel, 0.2, 50.05, now)
        except ImplausibleJump:  # pragma: no cover
            pytest.fail("a normal hour of steaming was rejected")

    def test_a_teleport_is_rejected(self):
        from src.core.services.vessel_tracking_service import ImplausibleJump

        now = utc_now()
        vessel = self._vessel(0.0, 50.0, now - timedelta(minutes=10))
        with pytest.raises(ImplausibleJump):
            self._service()._check_plausible(vessel, -70.0, 40.0, now)

    def test_a_burst_of_reports_is_not_mistaken_for_a_teleport(self):
        # Two fixes a second apart must not imply an impossible speed just
        # because the interval is tiny.
        from src.core.services.vessel_tracking_service import ImplausibleJump

        now = utc_now()
        vessel = self._vessel(0.0, 50.0, now - timedelta(seconds=1))
        try:
            self._service()._check_plausible(vessel, 0.001, 50.0005, now)
        except ImplausibleJump:  # pragma: no cover
            pytest.fail("a rapid pair of good fixes was rejected")

    def test_first_ever_fix_is_always_accepted(self):
        vessel = self._vessel(0.0, 50.0, None)
        vessel.current_position = None
        self._service()._check_plausible(vessel, 100.0, -40.0, utc_now())
