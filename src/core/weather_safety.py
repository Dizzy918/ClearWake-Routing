"""Deciding whether the weather is too much for a particular ship.

The rule that matters: a vessel with no engine cannot motor out of trouble.
A container ship in a thunderstorm is uncomfortable and burns more fuel; a
tall ship in the same squall may be unable to steer at all, and lightning over
a steel rig is its own hazard. So severe conditions are a *hard block* for
wind-dependent vessels and a *penalty* for everyone else.

Thresholds follow the Beaufort and Douglas scales rather than invented
numbers, so an operator can reason about them.

WMO weather codes used below (the subset that matters at sea):
    95, 96, 99 — thunderstorm, thunderstorm with hail
    82         — violent rain showers
    75         — heavy snowfall
"""

from __future__ import annotations

from typing import Optional

from src.models.vessel_types import is_wind_dependent

# Beaufort 8 — gale. The point at which small craft are in real trouble and
# even a large sailing rig must reduce to storm canvas.
GALE_WIND_KMH = 62.0
# Beaufort 10 — storm. Dangerous for anything that is not a large ship.
STORM_WIND_KMH = 89.0
# Douglas sea state 6 — very rough.
ROUGH_SEA_M = 4.0
HIGH_SEA_M = 6.0

THUNDERSTORM_CODES = frozenset({95, 96, 99})
VIOLENT_PRECIPITATION_CODES = frozenset({82, 75})

SEVERITY_CALM = "calm"
SEVERITY_MODERATE = "moderate"
SEVERITY_ROUGH = "rough"
SEVERITY_SEVERE = "severe"

# What each severity costs an engine-driven vessel, as a multiplier on the
# leg's cost. Sailing vessels are blocked outright at SEVERE.
SEVERITY_PENALTY = {
    SEVERITY_CALM: 1.0,
    SEVERITY_MODERATE: 1.05,
    SEVERITY_ROUGH: 1.25,
    SEVERITY_SEVERE: 1.8,
}


def classify(
    wind_speed_kmh: Optional[float] = None,
    wave_height_m: Optional[float] = None,
    weather_code: Optional[int] = None,
) -> str:
    """Grade conditions from calm to severe.

    Missing values are treated as calm: an absent forecast must not silently
    close a route. Callers that need certainty should check for the data.
    """
    severe = (
        (weather_code in THUNDERSTORM_CODES)
        or (wind_speed_kmh is not None and wind_speed_kmh >= STORM_WIND_KMH)
        or (wave_height_m is not None and wave_height_m >= HIGH_SEA_M)
    )
    if severe:
        return SEVERITY_SEVERE

    rough = (
        (weather_code in VIOLENT_PRECIPITATION_CODES)
        or (wind_speed_kmh is not None and wind_speed_kmh >= GALE_WIND_KMH)
        or (wave_height_m is not None and wave_height_m >= ROUGH_SEA_M)
    )
    if rough:
        return SEVERITY_ROUGH

    moderate = (wind_speed_kmh is not None and wind_speed_kmh >= 39.0) or (
        wave_height_m is not None and wave_height_m >= 2.5
    )
    return SEVERITY_MODERATE if moderate else SEVERITY_CALM


def is_passable(vessel_type: Optional[str], severity: str) -> bool:
    """Whether this vessel may enter water in this condition.

    Only wind-dependent vessels are stopped, and only by severe weather.
    Everything else is allowed through and simply penalised — a master with
    an engine gets to make that judgement, the router should not refuse it.
    """
    if severity != SEVERITY_SEVERE:
        return True
    return not is_wind_dependent(vessel_type)


def penalty_for(vessel_type: Optional[str], severity: str) -> float:
    """Cost multiplier for crossing water in this condition."""
    base = SEVERITY_PENALTY.get(severity, 1.0)
    if severity in (SEVERITY_ROUGH, SEVERITY_SEVERE) and is_wind_dependent(vessel_type):
        # A sailing vessel is disproportionately slowed before it is stopped.
        return base * 1.5
    return base


def describe(severity: str, vessel_type: Optional[str] = None) -> str:
    """One line an operator can read on the map."""
    text = {
        SEVERITY_CALM: "Calm to slight",
        SEVERITY_MODERATE: "Moderate",
        SEVERITY_ROUGH: "Rough — gale force or high seas",
        SEVERITY_SEVERE: "Severe — storm, thunderstorms or very high seas",
    }.get(severity, severity)
    if vessel_type and not is_passable(vessel_type, severity):
        text += " — impassable without engine power"
    return text
