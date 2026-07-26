from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any

class ZoneCreateSchema(BaseModel):
    name: str
    zone_type: str
    status: Optional[str] = "active"
    geometry: Dict[str, Any]
    description: Optional[str] = None

    class Config:
        from_attributes = True


class ZoneUpdateSchema(BaseModel):
    name: Optional[str] = None
    zone_type: Optional[str] = None
    status: Optional[str] = None
    geometry: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True


class CircularZoneSchema(BaseModel):
    """A keep-clear circle — the quickest way to close an area."""

    name: str = Field(..., min_length=1, max_length=200)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_nm: float = Field(..., gt=0, le=500)
    zone_type: str = Field(default="hazard")
    status: str = Field(default="inactive", pattern="^(active|inactive)$")
    description: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("zone_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        from src.models.zone import ZONE_TYPES

        if value not in ZONE_TYPES:
            raise ValueError(f"zone_type must be one of: {', '.join(ZONE_TYPES)}")
        return value
