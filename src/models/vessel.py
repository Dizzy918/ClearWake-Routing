import mongoengine as me
from datetime import datetime
from typing import Type
from src.core.time_utils import utc_now


VESSEL_TYPES: tuple[str, ...] = (
    "tanker",
    "container_ship",
    "bulk_carrier",
    "passenger_ship",
    "ferry",
    "ro_ro_ship",
    "lng_carrier",
    "lpg_carrier",
    "chemical_tanker",
    "car_carrier",
    "general_cargo",
    "offshore_support",
    "research_vessel",
    "icebreaker",
    "tugboat",
    "fishing_vessel",
    "cruise_ship",
    "yacht",
    "patrol_boat",
    "dredger",
)


def format_vessel_type_label(vessel_type: str) -> str:
    return vessel_type.replace("_", " ").title()


VESSEL_TYPE_OPTIONS = [
    {"value": vessel_type, "label": format_vessel_type_label(vessel_type)}
    for vessel_type in VESSEL_TYPES
]


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


class Vessel(me.Document):
    company_id = me.ObjectIdField(required=True)
    name = me.StringField(required=True)
    imo_number = me.StringField(required=True, unique=True)
    vessel_type = me.StringField(choices=VESSEL_TYPES)
    specs = me.EmbeddedDocumentField(VesselSpecs)
    fuel_consumption_rate = me.FloatField()
    current_status = me.StringField(choices=["idle", "en_route", "docked"], default="idle")
    current_position = me.PointField()
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
        """Calculate expected fuel burn for a distance in nautical miles."""
        return self._rate() * distance_nm

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
