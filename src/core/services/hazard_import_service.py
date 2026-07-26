"""Importing real closed and dangerous maritime areas.

Two public sources, no API key between them:

**NGA Maritime Safety Information** (``msi.nga.mil``) publishes the worldwide
navigational warnings that ships actually navigate by — piracy, live firing,
drifting hazards, closed areas. The warnings carry their positions inside the
message text rather than as geometry, so positions are parsed out and buffered
into circular zones. That is how a mariner treats them anyway: a warning is a
point plus a radius to keep clear of.

**Marine Regions** (``geo.vliz.be``) serves maritime boundaries as proper
GeoJSON polygons, for territorial and EEZ restrictions.

Imports are idempotent: a zone is keyed by source and external id, so
re-running updates rather than duplicates.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Iterable, Optional

from src.core.time_utils import utc_now
from src.models.zone import Zone

logger = logging.getLogger(__name__)

NGA_WARNINGS_URL = "https://msi.nga.mil/api/publications/broadcast-warn"
MARINE_REGIONS_WFS = "https://geo.vliz.be/geoserver/MarineRegions/wfs"

# msi.nga.mil rejects the default urllib/httpx agent.
_USER_AGENT = "ClearWakeRouting/1.0 (maritime route planning)"

# "19-23.0N 092-03.1W" — degrees-minutes with a hemisphere suffix.
_DMS = re.compile(
    r"(\d{1,3})-(\d{1,2}(?:\.\d+)?)\s*([NS])\s+(\d{1,3})-(\d{1,2}(?:\.\d+)?)\s*([EW])",
    re.IGNORECASE,
)

# What a warning is about, inferred from its wording. Order matters: the first
# match wins, so the more specific phrases come first.
_TYPE_KEYWORDS = (
    ("piracy", ("PIRACY", "PIRATE", "ARMED ROBBERY", "SKIFF")),
    ("military", ("FIRING", "MISSILE", "NAVAL EXERCISE", "MILITARY", "GUNNERY", "ORDNANCE")),
    ("hazard", ("WRECK", "OBSTRUCTION", "DERELICT", "ADRIFT", "UNLIT", "BUOY", "DEBRIS")),
    ("environmental", ("OIL SPILL", "POLLUTION", "ICE", "VOLCANIC", "ASH")),
    ("restricted", ("RESTRICTED", "PROHIBITED", "CLOSED AREA", "SECURITY ZONE")),
)

# Default keep-clear radius by type, in nautical miles. Navigational warnings
# rarely state an extent; these are conservative working values.
_DEFAULT_RADIUS_NM = {
    "piracy": 30.0,
    "military": 20.0,
    "hazard": 5.0,
    "environmental": 15.0,
    "restricted": 10.0,
}

_NM_PER_DEGREE = 60.0


def _fetch_json(url: str, params: dict | None = None, timeout: float = 30.0):
    import httpx

    with httpx.Client(timeout=timeout, headers={"User-Agent": _USER_AGENT}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def parse_positions(text: str) -> list[tuple[float, float]]:
    """Every lat/lon pair mentioned in a warning, as (lon, lat)."""
    out = []
    for deg_lat, min_lat, ns, deg_lon, min_lon, ew in _DMS.findall(text or ""):
        lat = int(deg_lat) + float(min_lat) / 60.0
        lon = int(deg_lon) + float(min_lon) / 60.0
        if ns.upper() == "S":
            lat = -lat
        if ew.upper() == "W":
            lon = -lon
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lon, lat))
    return out


def classify_warning(text: str) -> str:
    upper = (text or "").upper()
    for zone_type, keywords in _TYPE_KEYWORDS:
        if any(keyword in upper for keyword in keywords):
            return zone_type
    return "hazard"


def circle_polygon(lon: float, lat: float, radius_nm: float, segments: int = 24) -> dict:
    """A GeoJSON polygon approximating a keep-clear circle.

    Longitude degrees shrink with latitude, so the radius is scaled by
    cos(lat) east-west; without that, a circle near the poles would be drawn
    far too wide.
    """
    radius_deg = radius_nm / _NM_PER_DEGREE
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    ring = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        ring.append(
            [
                round(lon + (radius_deg / cos_lat) * math.cos(angle), 6),
                round(lat + radius_deg * math.sin(angle), 6),
            ]
        )
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


class HazardImportService:
    """Pulls public hazard data into the Zone collection."""

    def import_nga_warnings(
        self,
        limit: Optional[int] = None,
        activate: bool = False,
        types: Optional[Iterable[str]] = None,
    ) -> dict:
        """Import active NGA navigational warnings as keep-clear zones.

        Imported inactive by default. These are advisory and coarse — an
        operator should look at them before they start diverting ships.
        """
        payload = _fetch_json(NGA_WARNINGS_URL, {"status": "active", "output": "json"})
        warnings = payload.get("broadcast-warn", [])
        wanted = set(types) if types else None

        created = updated = skipped = 0
        for warning in warnings:
            text = warning.get("text") or ""
            positions = parse_positions(text)
            if not positions:
                skipped += 1  # nothing to place on a map
                continue

            zone_type = classify_warning(text)
            if wanted and zone_type not in wanted:
                skipped += 1
                continue

            external_id = (
                f"nga:{warning.get('navArea')}:{warning.get('msgYear')}:{warning.get('msgNumber')}"
            )
            # One zone per warning, centred on its first position.
            lon, lat = positions[0]
            radius = _DEFAULT_RADIUS_NM.get(zone_type, 10.0)

            name = self._name_for(warning, zone_type)
            existing = Zone.objects(source="nga_msi", external_id=external_id).first()
            document = {
                "name": name,
                "zone_type": zone_type,
                "status": "active" if activate else "inactive",
                "geometry": circle_polygon(lon, lat, radius),
                "description": text.strip()[:2000],
                "source": "nga_msi",
                "external_id": external_id,
                "source_url": NGA_WARNINGS_URL,
                "imported_at": utc_now(),
            }

            if existing:
                for key, value in document.items():
                    setattr(existing, key, value)
                existing.save()
                updated += 1
            else:
                Zone(**document).save()
                created += 1

            if limit and (created + updated) >= limit:
                break

        result = {
            "source": "NGA Maritime Safety Information",
            "fetched": len(warnings),
            "created": created,
            "updated": updated,
            "skipped_no_position": skipped,
            "activated": activate,
        }
        logger.info("NGA import: %s", result)
        return result

    @staticmethod
    def _name_for(warning: dict, zone_type: str) -> str:
        text = (warning.get("text") or "").strip()
        # Warnings open with the sea area, e.g. "EASTERN CARIBBEAN SEA."
        head = re.split(r"[.\n]", text, maxsplit=1)[0].strip()
        head = re.sub(r"^\d+\.\s*", "", head)[:70] or zone_type.title()
        return f"{head} (NAVAREA {warning.get('navArea', '?')})"

    def import_marine_regions(self, mrgids: Iterable[int], activate: bool = False) -> dict:
        """Import specific maritime boundaries as polygon zones.

        Boundaries are large; importing the whole world would swamp the map,
        so callers name the ones they care about by Marine Regions id.
        """
        created = updated = 0
        for mrgid in mrgids:
            payload = _fetch_json(
                MARINE_REGIONS_WFS,
                {
                    "service": "WFS",
                    "version": "1.0.0",
                    "request": "GetFeature",
                    "typeName": "MarineRegions:eez",
                    "outputFormat": "application/json",
                    "cql_filter": f"mrgid={int(mrgid)}",
                },
            )
            for feature in payload.get("features", []):
                properties = feature.get("properties", {})
                geometry = feature.get("geometry")
                if not geometry:
                    continue
                external_id = f"marineregions:{properties.get('mrgid')}"
                document = {
                    "name": properties.get("geoname") or f"EEZ {properties.get('mrgid')}",
                    "zone_type": "restricted",
                    "status": "active" if activate else "inactive",
                    "geometry": geometry,
                    "description": f"{properties.get('pol_type', 'maritime boundary')} "
                    f"(Marine Regions mrgid {properties.get('mrgid')})",
                    "source": "marine_regions",
                    "external_id": external_id,
                    "source_url": MARINE_REGIONS_WFS,
                    "imported_at": utc_now(),
                }
                existing = Zone.objects(source="marine_regions", external_id=external_id).first()
                if existing:
                    for key, value in document.items():
                        setattr(existing, key, value)
                    existing.save()
                    updated += 1
                else:
                    Zone(**document).save()
                    created += 1

        result = {"source": "Marine Regions", "created": created, "updated": updated}
        logger.info("Marine Regions import: %s", result)
        return result
