"""
Weather router — proxies marine weather from Open-Meteo (free, no API key).

Endpoints
---------
GET /api/v1/weather/marine?lat=…&lon=…
    Current marine + atmospheric conditions for a single coordinate.

GET /api/v1/weather/route?points=lat1,lon1;lat2,lon2;…
    Weather sampled at up to 10 evenly-spaced points along a route.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from src.core.time_utils import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])

_MARINE_BASE = "https://marine-api.open-meteo.com/v1/marine"
_WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"

# Fields we request from Open-Meteo.
#
# Both the click widget and the regional cards now read from the same
# `current=` endpoint so they always show identical values for the same
# coordinate. We keep the hourly lists as a fallback when the upstream
# response is missing the `current` block.
_MARINE_FIELDS = [
    "wave_height",
    "wave_direction",
    "wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
]

_WEATHER_FIELDS = [
    "wind_speed_10m",
    "wind_direction_10m",
    "temperature_2m",
    "weather_code",
]

# Legacy aliases kept for tests / external imports.
_MARINE_HOURLY = _MARINE_FIELDS
_WEATHER_HOURLY = _WEATHER_FIELDS

_TIMEOUT = 8.0  # seconds

# Shared async client with connection pooling. Reusing one client across
# requests cuts TCP/TLS handshake overhead and lets httpx keep idle
# connections warm — biggest single win on the /weather/regions cold path.
_async_client: Optional[httpx.AsyncClient] = None
_client_lock_owner: str = ""  # tracks which event loop owns the client


def _get_client() -> httpx.AsyncClient:
    """Return a process-wide shared httpx.AsyncClient.

    FastAPI may run on different event loops in some test/dev setups, so
    if the loop changes we transparently rebuild the client.
    """
    global _async_client, _client_lock_owner
    import asyncio
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    owner_id = id(current_loop) if current_loop else 0
    if _async_client is None or _client_lock_owner != owner_id:
        if _async_client is not None:
            try:
                # Detached loops — closing is best-effort.
                pass
            except Exception:
                pass
        _async_client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        _client_lock_owner = owner_id
    return _async_client


def _pick_current_values(hourly: dict, fields: list[str]) -> dict:
    """
    Return the value of each *field* at the current UTC hour.

    Open-Meteo returns the full day starting at 00:00 UTC, so index 0 is
    *not* "now" — it is midnight.  We match the current hour against the
    ``time`` array and fall back to the first sample if anything is off.
    """
    import datetime as _dt

    out: dict = {}
    times = hourly.get("time") if isinstance(hourly, dict) else None
    idx = 0
    if isinstance(times, list) and times:
        now_hour = _dt.utc_now().strftime("%Y-%m-%dT%H:00")
        try:
            idx = times.index(now_hour)
        except ValueError:
            # Fall back to the most recent past hour we have.
            idx = 0
            for i, t in enumerate(times):
                if isinstance(t, str) and t <= now_hour:
                    idx = i
                else:
                    break

    for field in fields:
        values = hourly.get(field)
        if isinstance(values, list) and values:
            out[field] = values[idx] if idx < len(values) else values[-1]
        else:
            out[field] = None
    return out


def _extract_current_block(payload: dict, fields: list[str]) -> dict:
    """
    Prefer Open-Meteo's ``current`` block (single value per field). Fall
    back to the matching hourly index if the upstream omits it.
    """
    out: dict = {}
    current = payload.get("current") if isinstance(payload, dict) else None
    if isinstance(current, dict):
        for field in fields:
            out[field] = current.get(field)
        # Pick up anything still missing from the hourly fallback.
        missing = [f for f in fields if out.get(f) is None]
        if missing:
            hourly = payload.get("hourly", {}) or {}
            out.update(_pick_current_values(hourly, missing))
        return out

    hourly = payload.get("hourly", {}) or {}
    return _pick_current_values(hourly, fields)


def _weather_code_label(code: Optional[int]) -> str:
    """Human-readable label for WMO weather-code subsets relevant at sea."""
    if code is None:
        return "Unknown"
    labels = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return labels.get(code, f"Code {code}")


async def _fetch_point_weather(lat: float, lon: float) -> dict:
    """Fetch marine + atmospheric data for a single point."""
    client = _get_client()
    marine_params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(_MARINE_FIELDS),
        "hourly": ",".join(_MARINE_FIELDS),
        "forecast_days": 1,
        "timezone": "UTC",
    }
    weather_params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(_WEATHER_FIELDS),
        "hourly": ",".join(_WEATHER_FIELDS),
        "forecast_days": 1,
        "timezone": "UTC",
    }

    marine_resp, weather_resp = await _parallel_get(
        client,
        [(_MARINE_BASE, marine_params), (_WEATHER_BASE, weather_params)],
    )

    result: dict = {"lat": lat, "lon": lon}

    if marine_resp:
        result.update(_extract_current_block(marine_resp, _MARINE_FIELDS))

    if weather_resp:
        atmo = _extract_current_block(weather_resp, _WEATHER_FIELDS)
        result.update(atmo)
        result["weather_label"] = _weather_code_label(atmo.get("weather_code"))

    return result


async def _parallel_get(
    client: httpx.AsyncClient,
    requests: List[tuple],
) -> list:
    """Fire multiple GETs concurrently; return parsed JSON or None per request."""
    import asyncio

    async def _get(url: str, params: dict):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Open-Meteo request failed (%s): %s", url, exc)
            return None

    return await asyncio.gather(*[_get(url, params) for url, params in requests])


def _beaufort_scale(wind_speed_kmh: Optional[float]) -> dict:
    """Return Beaufort number + label for a given wind speed in km/h."""
    if wind_speed_kmh is None:
        return {"number": None, "label": "Unknown"}
    thresholds = [
        (1, 0, "Calm"),
        (6, 1, "Light air"),
        (12, 2, "Light breeze"),
        (20, 3, "Gentle breeze"),
        (29, 4, "Moderate breeze"),
        (39, 5, "Fresh breeze"),
        (50, 6, "Strong breeze"),
        (62, 7, "Near gale"),
        (75, 8, "Gale"),
        (89, 9, "Strong gale"),
        (103, 10, "Storm"),
        (118, 11, "Violent storm"),
    ]
    for limit, number, label in thresholds:
        if wind_speed_kmh < limit:
            return {"number": number, "label": label}
    return {"number": 12, "label": "Hurricane force"}


def _sea_state(wave_height: Optional[float]) -> dict:
    """Return Douglas sea-state number + label."""
    if wave_height is None:
        return {"number": None, "label": "Unknown"}
    thresholds = [
        (0.0, 0, "Calm (glassy)"),
        (0.1, 1, "Calm (rippled)"),
        (0.5, 2, "Smooth"),
        (1.25, 3, "Slight"),
        (2.5, 4, "Moderate"),
        (4.0, 5, "Rough"),
        (6.0, 6, "Very rough"),
        (9.0, 7, "High"),
        (14.0, 8, "Very high"),
    ]
    for limit, number, label in thresholds:
        if wave_height <= limit:
            return {"number": number, "label": label}
    return {"number": 9, "label": "Phenomenal"}



_REGIONS_CACHE_TTL = 600  # 10 minutes in seconds
_regions_cache: list[dict] = []
_regions_cache_time: float = 0.0


async def _close_client() -> None:
    """Close the shared httpx client on FastAPI shutdown."""
    global _async_client
    if _async_client is not None:
        try:
            await _async_client.aclose()
        except Exception:
            pass
        _async_client = None



@router.get("/regions")
async def get_all_regions_weather():
    """Return current weather for all predefined maritime regions.

    Results are cached in-memory for 10 minutes to avoid excessive
    upstream API calls.
    """
    import asyncio
    import time

    from src.core.weather_regions import MARITIME_REGIONS

    global _regions_cache, _regions_cache_time  # noqa: PLW0603

    now = time.time()
    if _regions_cache and (now - _regions_cache_time) < _REGIONS_CACHE_TTL:
        logger.debug("Returning cached region weather (%d regions)", len(_regions_cache))
        return _regions_cache

    results = await asyncio.gather(
        *[_fetch_point_weather(r["lat"], r["lon"]) for r in MARITIME_REGIONS],
        return_exceptions=True,
    )

    region_weather: list[dict] = []
    for region, result in zip(MARITIME_REGIONS, results):
        if isinstance(result, Exception):
            logger.warning(
                "Weather fetch failed for region %s: %s", region["id"], result,
            )
            continue

        result["region_id"] = region["id"]
        result["region_name"] = region["name"]
        result["bbox"] = region["bbox"]
        result["beaufort"] = _beaufort_scale(result.get("wind_speed_10m"))
        result["sea_state"] = _sea_state(result.get("wave_height"))
        region_weather.append(result)

    _regions_cache = region_weather
    _regions_cache_time = time.time()

    logger.info("Fetched weather for %d / %d regions", len(region_weather), len(MARITIME_REGIONS))
    return region_weather


@router.get("/marine")
async def get_marine_weather(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
):
    """Return current marine weather for a single coordinate."""
    try:
        data = await _fetch_point_weather(lat, lon)
    except Exception as exc:
        logger.exception("Weather fetch failed")
        raise HTTPException(status_code=502, detail=str(exc))

    data["beaufort"] = _beaufort_scale(data.get("wind_speed_10m"))
    data["sea_state"] = _sea_state(data.get("wave_height"))
    return data


@router.get("/route")
async def get_route_weather(
    points: str = Query(
        ...,
        description="Semicolon-separated lat,lon pairs: 'lat1,lon1;lat2,lon2;…'",
    ),
):
    """Return weather sampled at up to 10 evenly-spaced points along a route."""
    try:
        raw_pairs = [p.strip() for p in points.split(";") if p.strip()]
        coords = []
        for pair in raw_pairs:
            parts = pair.split(",")
            if len(parts) != 2:
                raise ValueError(f"Invalid coordinate pair: {pair}")
            coords.append((float(parts[0]), float(parts[1])))
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=f"Bad points format: {exc}")

    if not coords:
        raise HTTPException(status_code=400, detail="No coordinates provided")

    if len(coords) > 10:
        step = len(coords) / 10
        coords = [coords[int(i * step)] for i in range(10)]

    import asyncio

    results = await asyncio.gather(
        *[_fetch_point_weather(lat, lon) for lat, lon in coords],
        return_exceptions=True,
    )

    weather_points = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Route weather point failed: %s", r)
            continue
        r["beaufort"] = _beaufort_scale(r.get("wind_speed_10m"))
        r["sea_state"] = _sea_state(r.get("wave_height"))
        weather_points.append(r)

    wave_heights = [p["wave_height"] for p in weather_points if p.get("wave_height") is not None]
    wind_speeds = [p["wind_speed_10m"] for p in weather_points if p.get("wind_speed_10m") is not None]

    summary = {
        "point_count": len(weather_points),
        "max_wave_height": max(wave_heights) if wave_heights else None,
        "avg_wave_height": round(sum(wave_heights) / len(wave_heights), 2) if wave_heights else None,
        "max_wind_speed": max(wind_speeds) if wind_speeds else None,
        "avg_wind_speed": round(sum(wind_speeds) / len(wind_speeds), 1) if wind_speeds else None,
    }

    if wave_heights:
        summary["worst_sea_state"] = _sea_state(max(wave_heights))
    if wind_speeds:
        summary["worst_beaufort"] = _beaufort_scale(max(wind_speeds))

    return {"summary": summary, "points": weather_points}



@router.get("/currents")
async def get_ocean_currents(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
):
    """Return estimated ocean current for a single coordinate.

    Surface current is approximated via Stokes drift from wave
    parameters (height, period, direction).
    """
    from src.core.grib_parser import fetch_current_at_point

    try:
        current = await fetch_current_at_point(lat, lon)
        return {
            "lat": lat,
            "lon": lon,
            "u_ms": round(current.u_ms, 4),
            "v_ms": round(current.v_ms, 4),
            "speed_knots": round(current.speed_knots, 2),
            "direction_deg": round(current.direction_deg, 1),
            "speed_ms": round(current.speed_ms, 3),
        }
    except Exception as exc:
        logger.exception("Current fetch failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/currents/grid")
async def get_currents_grid(
    min_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lat: float = Query(..., ge=-90, le=90),
    max_lon: float = Query(..., ge=-180, le=180),
    step_deg: float = Query(1.0, gt=0.1, le=5.0, description="Grid step in degrees"),
):
    """
    Return an ocean-current U/V grid for the given bounding box.

    If a GRIB2 (or JSON-backed) :class:`CurrentGrid` is configured via
    ``settings.CURRENT_GRID_PATH``, the data is interpolated from it.
    Otherwise the endpoint falls back to the existing Stokes-drift
    estimate sampled at every grid point.
    """
    import os
    from src.core.services.current_grid import load_auto
    from src.core.grib_parser import fetch_currents_batch

    if max_lat <= min_lat or max_lon <= min_lon:
        raise HTTPException(status_code=400, detail="bbox max must exceed min")

    lats: list[float] = []
    lat = min_lat
    while lat <= max_lat + 1e-9:
        lats.append(round(lat, 4))
        lat += step_deg
    lons: list[float] = []
    lon = min_lon
    while lon <= max_lon + 1e-9:
        lons.append(round(lon, 4))
        lon += step_deg

    if len(lats) * len(lons) > 1000:
        raise HTTPException(
            status_code=400,
            detail="Requested grid too large (>1000 points). Increase step_deg.",
        )

    points = [(la, lo) for la in lats for lo in lons]

    grid_path = os.environ.get("CURRENT_GRID_PATH")
    grid = load_auto(grid_path) if grid_path else None

    results: list[dict] = []
    if grid is not None:
        for la, lo in points:
            u, v = grid.sample(la, lo)
            speed_ms = (u * u + v * v) ** 0.5
            results.append({
                "lat": la,
                "lon": lo,
                "u_ms": round(u, 4),
                "v_ms": round(v, 4),
                "speed_knots": round(speed_ms * 1.94384, 3),
            })
        source = "grib"
    else:
        currents = await fetch_currents_batch(points)
        for (la, lo), c in zip(points, currents):
            results.append({
                "lat": la,
                "lon": lo,
                "u_ms": round(c.u_ms, 4),
                "v_ms": round(c.v_ms, 4),
                "speed_knots": round(c.speed_knots, 3),
            })
        source = "stokes_drift"

    return {
        "source": source,
        "bbox": {"min_lat": min_lat, "min_lon": min_lon, "max_lat": max_lat, "max_lon": max_lon},
        "step_deg": step_deg,
        "point_count": len(results),
        "points": results,
    }


@router.get("/currents/route")
async def get_route_currents(
    points: str = Query(
        ...,
        description="Semicolon-separated lat,lon pairs: 'lat1,lon1;lat2,lon2;…'",
    ),
):
    """Return ocean currents sampled along a route."""
    from src.core.services.current_service import OceanCurrentService

    try:
        raw_pairs = [p.strip() for p in points.split(";") if p.strip()]
        waypoints: list[dict] = []
        for pair in raw_pairs:
            parts = pair.split(",")
            if len(parts) != 2:
                raise ValueError(f"Invalid pair: {pair}")
            waypoints.append(
                {"coordinates": [float(parts[1]), float(parts[0])]}
            )
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=f"Bad points format: {exc}")

    service = OceanCurrentService()
    results = await service.get_currents_for_route(waypoints)

    avg_speed = 0.0
    if results:
        avg_speed = sum(r["speed_knots"] for r in results) / len(results)

    return {
        "summary": {
            "point_count": len(results),
            "avg_current_speed_knots": round(avg_speed, 2),
        },
        "points": results,
    }
