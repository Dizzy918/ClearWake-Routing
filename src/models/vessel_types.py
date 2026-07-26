"""The vessel taxonomy: what a ship is called, what it burns, how it moves.

One table drives the type list, the human labels, the fuel multipliers and —
importantly for routing — whether a vessel can make way without wind.

Propulsion matters because a purely wind-driven vessel is not merely slower in
severe weather, it can be unable to steer out of it. Routing treats
``PROPULSION_SAIL`` as hard-blocked by severe conditions, while an engine-
driven vessel is only penalised. ``PROPULSION_SAIL_ASSISTED`` covers modern
wind-assisted cargo ships, which carry an engine and so are treated as
motorised for safety decisions.

Names follow ordinary commercial-shipping usage (the terms a broker or port
agent would use), not invented categories.
"""

from __future__ import annotations

PROPULSION_MOTOR = "motor"
PROPULSION_SAIL = "sail"
PROPULSION_SAIL_ASSISTED = "sail_assisted"

PROPULSION_KINDS: tuple[str, ...] = (
    PROPULSION_MOTOR,
    PROPULSION_SAIL,
    PROPULSION_SAIL_ASSISTED,
)

# category -> how the trade groups these ships
CATEGORIES: tuple[str, ...] = (
    "tanker",
    "dry_bulk",
    "container",
    "general_cargo",
    "passenger",
    "offshore",
    "specialised",
    "fishing",
    "service",
    "sailing",
)


def _spec(label, category, fuel_multiplier, propulsion=PROPULSION_MOTOR):
    return {
        "label": label,
        "category": category,
        "fuel_multiplier": fuel_multiplier,
        "propulsion": propulsion,
    }


# NOTE: every key that has ever been stored must stay in this table. The
# generic older names (tanker, container_ship, ...) are kept alongside the
# more specific ones so existing fleets keep validating.
VESSEL_TYPE_SPECS: dict[str, dict] = {
    # ---- tankers -------------------------------------------------------
    "tanker": _spec("Tanker", "tanker", 1.20),
    "crude_oil_tanker": _spec("Crude Oil Tanker", "tanker", 1.22),
    "product_tanker": _spec("Product Tanker", "tanker", 1.18),
    "chemical_tanker": _spec("Chemical Tanker", "tanker", 1.23),
    "lng_carrier": _spec("LNG Carrier", "tanker", 1.25),
    "lpg_carrier": _spec("LPG Carrier", "tanker", 1.22),
    "asphalt_tanker": _spec("Asphalt / Bitumen Tanker", "tanker", 1.21),
    "shuttle_tanker": _spec("Shuttle Tanker", "tanker", 1.24),
    # ---- dry bulk ------------------------------------------------------
    "bulk_carrier": _spec("Bulk Carrier", "dry_bulk", 1.15),
    "ore_carrier": _spec("Ore Carrier", "dry_bulk", 1.19),
    "cement_carrier": _spec("Cement Carrier", "dry_bulk", 1.16),
    "wood_chip_carrier": _spec("Wood Chip Carrier", "dry_bulk", 1.14),
    "self_discharging_bulker": _spec("Self-Discharging Bulker", "dry_bulk", 1.17),
    # ---- container -----------------------------------------------------
    "container_ship": _spec("Container Ship", "container", 1.10),
    "feeder_container_ship": _spec("Feeder Container Ship", "container", 1.08),
    "ultra_large_container_ship": _spec("Ultra Large Container Ship", "container", 1.12),
    # ---- general cargo -------------------------------------------------
    "general_cargo": _spec("General Cargo Ship", "general_cargo", 1.13),
    "multi_purpose_vessel": _spec("Multi-Purpose Vessel", "general_cargo", 1.14),
    "heavy_lift_vessel": _spec("Heavy Lift Vessel", "general_cargo", 1.26),
    "reefer": _spec("Refrigerated Cargo Ship", "general_cargo", 1.19),
    "livestock_carrier": _spec("Livestock Carrier", "general_cargo", 1.17),
    "ro_ro_ship": _spec("Ro-Ro Ship", "general_cargo", 1.14),
    "car_carrier": _spec("Vehicle Carrier", "general_cargo", 1.14),
    "barge_carrier": _spec("Barge Carrier", "general_cargo", 1.15),
    # ---- passenger -----------------------------------------------------
    "passenger_ship": _spec("Passenger Ship", "passenger", 1.18),
    "cruise_ship": _spec("Cruise Ship", "passenger", 1.17),
    "ferry": _spec("Ferry", "passenger", 1.12),
    "ropax": _spec("Ro-Pax Ferry", "passenger", 1.15),
    "high_speed_craft": _spec("High-Speed Craft", "passenger", 1.45),
    # ---- offshore ------------------------------------------------------
    "offshore_support": _spec("Offshore Support Vessel", "offshore", 1.30),
    "platform_supply_vessel": _spec("Platform Supply Vessel", "offshore", 1.29),
    "anchor_handling_tug": _spec("Anchor Handling Tug Supply", "offshore", 1.34),
    "drillship": _spec("Drillship", "offshore", 1.38),
    "well_intervention_vessel": _spec("Well Intervention Vessel", "offshore", 1.31),
    "wind_farm_service_vessel": _spec("Wind Farm Service Vessel", "offshore", 1.20),
    # ---- specialised ---------------------------------------------------
    "icebreaker": _spec("Icebreaker", "specialised", 1.40),
    "cable_layer": _spec("Cable Laying Vessel", "specialised", 1.27),
    "pipe_layer": _spec("Pipe Laying Vessel", "specialised", 1.33),
    "crane_vessel": _spec("Floating Crane Vessel", "specialised", 1.30),
    "semi_submersible_heavy_lift": _spec("Semi-Submersible Heavy Lift", "specialised", 1.36),
    "dredger": _spec("Dredger", "specialised", 1.32),
    "research_vessel": _spec("Research Vessel", "specialised", 1.20),
    "survey_vessel": _spec("Survey Vessel", "specialised", 1.18),
    "hospital_ship": _spec("Hospital Ship", "specialised", 1.19),
    # ---- fishing -------------------------------------------------------
    "fishing_vessel": _spec("Fishing Vessel", "fishing", 1.16),
    "trawler": _spec("Trawler", "fishing", 1.21),
    "purse_seiner": _spec("Purse Seiner", "fishing", 1.19),
    "factory_ship": _spec("Fish Factory Ship", "fishing", 1.24),
    # ---- harbour & service ---------------------------------------------
    "tugboat": _spec("Tugboat", "service", 1.35),
    "salvage_tug": _spec("Salvage Tug", "service", 1.37),
    "pilot_boat": _spec("Pilot Boat", "service", 1.25),
    "patrol_boat": _spec("Patrol Boat", "service", 1.28),
    "buoy_tender": _spec("Buoy Tender", "service", 1.22),
    "bunkering_tanker": _spec("Bunkering Tanker", "service", 1.20),
    # ---- wind powered ---------------------------------------------------
    # These are the reason routing cares about propulsion at all.
    "sailing_yacht": _spec("Sailing Yacht", "sailing", 0.0, PROPULSION_SAIL),
    "tall_ship": _spec("Tall Ship", "sailing", 0.0, PROPULSION_SAIL),
    "sail_training_ship": _spec("Sail Training Ship", "sailing", 0.0, PROPULSION_SAIL),
    "sailing_cargo_ship": _spec("Sailing Cargo Ship", "sailing", 0.0, PROPULSION_SAIL),
    "wind_assisted_cargo_ship": _spec(
        "Wind-Assisted Cargo Ship", "sailing", 0.85, PROPULSION_SAIL_ASSISTED
    ),
    "yacht": _spec("Motor Yacht", "sailing", 1.08),
}

VESSEL_TYPES: tuple[str, ...] = tuple(VESSEL_TYPE_SPECS)


def format_vessel_type_label(vessel_type: str) -> str:
    """Human label for a type, falling back to a tidied identifier."""
    spec = VESSEL_TYPE_SPECS.get(vessel_type)
    if spec:
        return spec["label"]
    return (vessel_type or "").replace("_", " ").title()


def fuel_multiplier_for(vessel_type: str) -> float:
    spec = VESSEL_TYPE_SPECS.get(vessel_type)
    return float(spec["fuel_multiplier"]) if spec else 1.0


def propulsion_for(vessel_type: str) -> str:
    spec = VESSEL_TYPE_SPECS.get(vessel_type)
    return spec["propulsion"] if spec else PROPULSION_MOTOR


def category_for(vessel_type: str) -> str:
    spec = VESSEL_TYPE_SPECS.get(vessel_type)
    return spec["category"] if spec else "general_cargo"


def is_wind_dependent(vessel_type: str) -> bool:
    """True when the vessel has no engine to fall back on.

    Wind-assisted ships are excluded: they carry propulsion and can motor
    clear of weather, so only true sail is treated as unable to.
    """
    return propulsion_for(vessel_type) == PROPULSION_SAIL


VESSEL_TYPE_OPTIONS = [
    {
        "value": vessel_type,
        "label": spec["label"],
        "category": spec["category"],
        "propulsion": spec["propulsion"],
        "wind_dependent": spec["propulsion"] == PROPULSION_SAIL,
    }
    for vessel_type, spec in VESSEL_TYPE_SPECS.items()
]
