"""Optimization router — draft / trim service."""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_dependencies import get_current_user

from src.core.services.draft_trim_optimizer import (
    DraftTrimInput,
    optimize as optimize_draft_trim,
)
from src.infrastructure.repositories.vessel_repository import VesselRepository
from src.schemas.optimization import DraftTrimRequest, DraftTrimResponse

router = APIRouter(prefix="/api/v1/optimization", tags=["optimization"], dependencies=[Depends(get_current_user)])
_vessels = VesselRepository()


def _resolve_dimensions(req: DraftTrimRequest) -> tuple[float, float, float]:
    """Return (length_m, beam_m, max_draft_m), preferring DB specs over req."""
    length = req.length_m
    beam = req.beam_m
    draft = req.max_draft_m

    if req.vessel_id and ObjectId.is_valid(req.vessel_id):
        vessel = _vessels.get_by_id(req.vessel_id)
        if vessel and vessel.specs:
            length = length or vessel.specs.length_m
            beam = beam or vessel.specs.beam_m
            draft = draft or vessel.specs.max_draft_m

    missing = [
        name for name, val in (
            ("length_m", length),
            ("beam_m", beam),
            ("max_draft_m", draft),
        )
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing vessel dimension(s): {', '.join(missing)}. "
                   "Provide them in the request body or a vessel_id with specs.",
        )
    return float(length), float(beam), float(draft)


@router.post("/draft-trim", response_model=DraftTrimResponse)
def calculate_draft_trim(request: DraftTrimRequest):
    """
    Optimise trim (and report mean / forward / aft drafts) for the given
    vessel + voyage conditions.

    Returns the trim that minimises a compact hull-resistance index,
    along with the equivalent fuel savings vs zero trim.
    """
    length, beam, max_draft = _resolve_dimensions(request)

    payload = DraftTrimInput(
        length_m=length,
        beam_m=beam,
        max_draft_m=max_draft,
        speed_knots=request.speed_knots,
        cargo_weight_t=request.cargo_weight_t,
        max_cargo_t=request.max_cargo_t,
        wave_height_m=request.wave_height_m,
        water_depth_m=request.water_depth_m,
    )

    try:
        result = optimize_draft_trim(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result.as_dict()
