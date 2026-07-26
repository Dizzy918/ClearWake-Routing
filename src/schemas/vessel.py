from pydantic import BaseModel, Field
from pydantic import field_validator
from typing import Optional

from src.models.vessel import VESSEL_TYPES


class VesselTypeValidationMixin(BaseModel):
    @field_validator("vessel_type", check_fields=False)
    @classmethod
    def validate_vessel_type(cls, vessel_type: Optional[str]) -> Optional[str]:
        if vessel_type is None:
            return vessel_type
        if vessel_type not in VESSEL_TYPES:
            allowed = ", ".join(VESSEL_TYPES)
            raise ValueError(f"Unsupported vessel_type '{vessel_type}'. Allowed values: {allowed}")
        return vessel_type


class VesselSpecsSchema(BaseModel):
    max_draft_m: Optional[float] = None
    max_speed_knots: Optional[float] = None
    length_m: Optional[float] = None
    beam_m: Optional[float] = None
    max_cargo_t: Optional[float] = None
    cargo_weight_t: Optional[float] = None
    trim_m: Optional[float] = None
    hydro_resistance_coef: Optional[float] = None


class VesselCreateSchema(VesselTypeValidationMixin):
    company_id: str
    name: str
    imo_number: str
    vessel_type: str
    specs: Optional[VesselSpecsSchema] = None
    fuel_consumption_rate: Optional[float] = None
    current_status: Optional[str] = "idle"


class VesselUpdateSchema(VesselTypeValidationMixin):
    company_id: Optional[str] = None
    name: Optional[str] = None
    imo_number: Optional[str] = None
    vessel_type: Optional[str] = None
    specs: Optional[VesselSpecsSchema] = None
    fuel_consumption_rate: Optional[float] = None
    current_status: Optional[str] = None


class VesselPositionSchema(BaseModel):
    """A single position report from AIS, GPS, or an operator."""

    lon: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)
    heading_deg: Optional[float] = Field(default=None, ge=0, le=360)
    speed_knots: Optional[float] = Field(default=None, ge=0, le=100)
    source: str = Field(default="gps")

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        from src.models.vessel_position import POSITION_SOURCES

        if value not in POSITION_SOURCES:
            raise ValueError(f"source must be one of: {', '.join(POSITION_SOURCES)}")
        return value


class VesselCourseSchema(BaseModel):
    """An order to send a vessel somewhere."""

    destination_port: str = Field(..., min_length=1)
    strategy: str = Field(default="fastest")
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        allowed = {"fastest", "eco"}
        if value not in allowed:
            raise ValueError(f"strategy must be one of: {', '.join(sorted(allowed))}")
        return value
