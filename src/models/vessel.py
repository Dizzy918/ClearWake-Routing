import mongoengine as me
from datetime import datetime
from typing import Type
from src.core.time_utils import utc_now


from src.models.vessel_types import (  # re-exported for existing importers
    PROPULSION_KINDS,
    PROPULSION_MOTOR,
    VESSEL_TYPE_OPTIONS,
    VESSEL_TYPE_SPECS,
    VESSEL_TYPES,
    category_for,
    format_vessel_type_label,
    fuel_multiplier_for,
    is_wind_dependent,
    propulsion_for,
)


class VesselSpecs(me.EmbeddedDocument):
    max_draft_m = me.FloatField()
    max_speed_knots = me.FloatField()
    length_m = me.FloatField()
    beam_m = me.FloatField()
    # Loading state + hull resistance — feed the draft/trim optimizer (issue #80).
    max_cargo_t = me.FloatField()
    cargo_weight_t = me.FloatField()
    trim_m = me.FloatField()
    hydro_resistance_coef = me.FloatField()


VESSEL_STATUSES: tuple[str, ...] = (
    "idle",
    "en_route",
    "docked",
    "anchored",
    "maintenance",
)


class Vessel(me.Document):
    company_id = me.ObjectIdField(required=True)
    name = me.StringField(required=True)
    imo_number = me.StringField(required=True, unique=True)
    vessel_type = me.StringField(choices=VESSEL_TYPES)
    specs = me.EmbeddedDocumentField(VesselSpecs)
    fuel_consumption_rate = me.FloatField()
    current_status = me.StringField(choices=VESSEL_STATUSES, default="idle")
    current_position = me.PointField()

    # ---- live tracking ----
    # Last reported course over ground (degrees true) and speed (knots), plus
    # when the report came in. Kept on the vessel so the fleet view is a
    # single query; the full breadcrumb trail lives in VesselPosition.
    heading_deg = me.FloatField(min_value=0, max_value=360)
    speed_knots = me.FloatField(min_value=0)
    position_updated_at = me.DateTimeField()

    # ---- current voyage ----
    # Set when a course is assigned; cleared when the voyage ends.
    active_route_id = me.ObjectIdField()
    destination_port = me.StringField()

    created_at = me.DateTimeField(default=utc_now)

    meta = {
        "collection": "vessels",
        "allow_inheritance": True,
        "indexes": ["company_id", "imo_number", "current_status",
                    {"fields": ["current_position"], "cls": False, "sparse": True}]
    }

    def _rate(self) -> float:
        return float(self.fuel_consumption_rate or 0.0)

    def calculate_fuel(self, distance_nm: float) -> float:
        """Expected fuel burn over a distance in nautical miles.

        Subclasses below hard-code their own multiplier for historical
        reasons; every type added since is driven by the taxonomy table, so
        the base implementation looks the factor up rather than defaulting
        to 1.0 and silently under-reporting.
        """
        return self._rate() * distance_nm * fuel_multiplier_for(self.vessel_type)

    @property
    def propulsion(self) -> str:
        return propulsion_for(self.vessel_type)

    @property
    def is_wind_dependent(self) -> bool:
        """No engine to fall back on — severe weather is a hard block."""
        return is_wind_dependent(self.vessel_type)

    @property
    def category(self) -> str:
        return category_for(self.vessel_type)

    @classmethod
    def build(cls, **kwargs):
        vessel_type = kwargs.get("vessel_type")
        subclass = _VESSEL_TYPE_MAPPING.get(vessel_type, cls)
        return subclass(**kwargs)


class Tanker(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.20


class ContainerShip(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.10


class BulkCarrier(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.15


class PassengerShip(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.18


class Ferry(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.12


class RoRoShip(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.14


class LNGCarrier(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.25


class LPGCarrier(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.22


class ChemicalTanker(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.23


class CarCarrier(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.14


class GeneralCargo(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.13


class OffshoreSupport(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.30


class ResearchVessel(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.20


class Icebreaker(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.40


class Tugboat(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.35


class FishingVessel(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.16


class CruiseShip(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.17


class Yacht(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.08


class PatrolBoat(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.28


class Dredger(Vessel):
    def calculate_fuel(self, distance_nm: float) -> float:
        return self._rate() * distance_nm * 1.32


_VESSEL_TYPE_MAPPING: dict[str, Type[Vessel]] = {
    "tanker": Tanker,
    "container_ship": ContainerShip,
    "bulk_carrier": BulkCarrier,
    "passenger_ship": PassengerShip,
    "ferry": Ferry,
    "ro_ro_ship": RoRoShip,
    "lng_carrier": LNGCarrier,
    "lpg_carrier": LPGCarrier,
    "chemical_tanker": ChemicalTanker,
    "car_carrier": CarCarrier,
    "general_cargo": GeneralCargo,
    "offshore_support": OffshoreSupport,
    "research_vessel": ResearchVessel,
    "icebreaker": Icebreaker,
    "tugboat": Tugboat,
    "fishing_vessel": FishingVessel,
    "cruise_ship": CruiseShip,
    "yacht": Yacht,
    "patrol_boat": PatrolBoat,
    "dredger": Dredger,
}
