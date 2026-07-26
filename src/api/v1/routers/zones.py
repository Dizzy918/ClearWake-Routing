import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_dependencies import get_current_user

from src.api.auth_dependencies import require_role
from src.core.events.dispatcher import dispatcher
from src.core.events.event import Event
from src.core.services.auto_reroute_service import AutoRerouteService
from src.core.services.hazard_import_service import HazardImportService, circle_polygon
from src.core.zone_presets import ZONE_PRESETS
from src.models.user import User
from src.infrastructure.repositories.zone_repositories import ZoneRepository
from src.models.zone import Zone as ZoneModel
from src.schemas.zone import CircularZoneSchema, ZoneCreateSchema, ZoneUpdateSchema

router = APIRouter(prefix="/api/v1/zones", tags=["zones"], dependencies=[Depends(get_current_user)])
repo = ZoneRepository()
auto_reroute = AutoRerouteService()
hazard_import = HazardImportService()
logger = logging.getLogger(__name__)


def _assert_can_edit(zone_id: str, user) -> None:
    """403 when a company tries to change a zone that is not its to change.

    Shared zones affect every operator's routing, so only an admin may open or
    close one; a company's private zones are its own business.
    """
    zone = repo.get_by_id(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not repo.is_editable_by(zone, user):
        raise HTTPException(
            status_code=403,
            detail="This zone belongs to another operator, or is a shared zone that only an admin can change",
        )


def _normalize_lon(lon: float) -> float:
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def _normalize_geometry_coordinates(coordinates):
    if not isinstance(coordinates, list):
        return coordinates

    if coordinates and isinstance(coordinates[0], (int, float)) and len(coordinates) >= 2:
        return [_normalize_lon(float(coordinates[0])), float(coordinates[1])]

    return [_normalize_geometry_coordinates(item) for item in coordinates]


def _normalize_geometry(geometry: dict | None):
    if not geometry or "coordinates" not in geometry:
        return geometry

    normalized = dict(geometry)
    normalized["coordinates"] = _normalize_geometry_coordinates(geometry["coordinates"])
    return normalized


@router.get("/")
def get_zones(
    status: str | None = Query(default=None),
    zone_type: str | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    """Zones this operator can see: the shared ones plus its own.

    Shared zones (no company_id) are the official closures, imported hazards
    and chokepoint presets — facts about the sea. Private zones belong to one
    operator and are invisible to the rest.
    """
    if status == "active":
        zones = repo.active_visible_to(user.company_id)
    else:
        zones = repo.visible_to(user.company_id)
        if zone_type:
            zones = [z for z in zones if z.zone_type == zone_type]

    return [json.loads(zone.to_json()) for zone in zones]


@router.get("/{zone_id}")
def get_zone_by_id(zone_id: str):
    zone = repo.get_by_id(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    return json.loads(zone.to_json())


@router.post("/")
def create_zone(
    zone_in: ZoneCreateSchema,
    user: User = Depends(require_role("admin", "operator")),
):
    payload = zone_in.model_dump(exclude_unset=True)
    payload["geometry"] = _normalize_geometry(payload.get("geometry"))
    zone = ZoneModel(**payload)
    created = repo.create(zone)
    return json.loads(created.to_json())


@router.patch("/{zone_id}")
def update_zone(
    zone_id: str,
    zone_in: ZoneUpdateSchema,
    user: User = Depends(require_role("admin", "operator")),
):
    payload = zone_in.model_dump(exclude_unset=True)
    if "geometry" in payload:
        payload["geometry"] = _normalize_geometry(payload.get("geometry"))
    updated = repo.update(zone_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Zone not found")
    return json.loads(updated.to_json())


@router.post("/{zone_id}/activate")
def activate_zone(
    zone_id: str,
    user: User = Depends(require_role("admin", "operator")),
):
    """Close a zone, and re-plan every voyage that was sailing through it.

    Closing a canal is the case this exists for: the routes already committed
    to it become invalid the moment it shuts, and an operator should be told
    which ships are affected and where they are now headed instead — not
    discover it later.
    """
    zone = repo.activate(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    payload = json.loads(zone.to_json())
    try:
        payload["auto_reroute"] = auto_reroute.handle_zone_activated(zone)
    except Exception:
        # Rerouting is a follow-on action; never fail the closure because of it.
        logger.exception("auto-reroute failed after activating zone %s", zone_id)
        payload["auto_reroute"] = {"error": "reroute evaluation failed; see server logs"}
    return payload


@router.post("/{zone_id}/deactivate")
def deactivate_zone(
    zone_id: str,
    user: User = Depends(require_role("admin", "operator")),
):
    """Reopen a zone. Existing courses stay as they are.

    A reopened canal does not automatically pull ships back onto it: they may
    be committed to the alternative, and re-planning a voyage without being
    asked is a decision for the operator.
    """
    zone = repo.deactivate(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    dispatcher.dispatch(Event("zone_opened", {
        "zone_id": str(zone.id),
        "zone_name": zone.name,
        "zone_type": zone.zone_type,
    }))
    return json.loads(zone.to_json())


@router.delete("/{zone_id}")
def delete_zone(
    zone_id: str,
    user: User = Depends(require_role("admin", "operator")),
):
    if not repo.delete(zone_id):
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Making zones easier to create
# ---------------------------------------------------------------------------

@router.get("/presets/templates")
def get_zone_presets():
    """Ready-made zone shapes so common closures are one click, not a drawing.

    Drawing an accurate polygon around a strait with a mouse is slow and
    error-prone. The chokepoints that actually get closed are a short list, so
    they ship as templates.
    """
    return ZONE_PRESETS


@router.post("/presets/{preset_id}")
def create_zone_from_preset(
    preset_id: str,
    status: str = Query(default="inactive", pattern="^(active|inactive)$"),
    user: User = Depends(require_role("admin", "operator")),
):
    """Create a zone from a preset, optionally closing it immediately."""
    preset = next((p for p in ZONE_PRESETS if p["id"] == preset_id), None)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {preset_id}")

    zone = ZoneModel(
        name=preset["name"],
        zone_type=preset["zone_type"],
        status=status,
        geometry=preset["geometry"],
        description=preset.get("description"),
    )
    created = repo.create(zone)
    payload = json.loads(created.to_json())
    if status == "active":
        try:
            payload["auto_reroute"] = auto_reroute.handle_zone_activated(created)
        except Exception:
            logger.exception("auto-reroute failed for preset zone %s", preset_id)
    return payload


@router.post("/circle")
def create_circular_zone(
    request: CircularZoneSchema,
    user: User = Depends(require_role("admin", "operator")),
):
    """Close a circle of a given radius around a point.

    The quickest way to express "keep clear of here", which is how most
    hazards are actually reported.
    """
    zone = ZoneModel(
        name=request.name,
        zone_type=request.zone_type,
        status=request.status,
        geometry=circle_polygon(request.lon, request.lat, request.radius_nm),
        description=request.description,
    )
    created = repo.create(zone)
    payload = json.loads(created.to_json())
    if request.status == "active":
        try:
            payload["auto_reroute"] = auto_reroute.handle_zone_activated(created)
        except Exception:
            logger.exception("auto-reroute failed for circular zone")
    return payload


# ---------------------------------------------------------------------------
# Real-world hazard data
# ---------------------------------------------------------------------------

@router.post("/import/nga")
def import_nga_warnings(
    limit: int = Query(default=200, ge=1, le=1000),
    activate: bool = Query(default=False),
    user: User = Depends(require_role("admin")),
):
    """Import live navigational warnings from NGA Maritime Safety Information.

    Imported inactive unless asked otherwise: these are advisory and coarse,
    and activating hundreds of areas at once would redirect a fleet without
    anyone having looked at them.
    """
    try:
        return hazard_import.import_nga_warnings(limit=limit, activate=activate)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NGA import failed: {exc}")


@router.post("/import/marine-regions")
def import_marine_regions(
    mrgids: list[int],
    activate: bool = Query(default=False),
    user: User = Depends(require_role("admin")),
):
    """Import maritime boundary polygons from Marine Regions by mrgid."""
    try:
        return hazard_import.import_marine_regions(mrgids, activate=activate)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Marine Regions import failed: {exc}")
