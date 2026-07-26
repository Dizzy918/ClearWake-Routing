import pytest
from pydantic import ValidationError

from src.models.vessel import VESSEL_TYPE_OPTIONS, VESSEL_TYPES, format_vessel_type_label
from src.schemas.vessel import VesselCreateSchema, VesselUpdateSchema


def test_vessel_type_options_match_model_choices():
    assert [option["value"] for option in VESSEL_TYPE_OPTIONS] == list(VESSEL_TYPES)


def test_vessel_type_label_uses_trade_conventional_naming():
    # Labels come from the taxonomy table, so they read the way the industry
    # writes them rather than as de-underscored identifiers.
    assert format_vessel_type_label("ro_ro_ship") == "Ro-Ro Ship"
    assert format_vessel_type_label("lng_carrier") == "LNG Carrier"
    assert format_vessel_type_label("anchor_handling_tug") == "Anchor Handling Tug Supply"


def test_unknown_type_still_gets_a_readable_label():
    assert format_vessel_type_label("space_freighter") == "Space Freighter"


def test_every_type_declares_propulsion_and_category():
    from src.models.vessel_types import CATEGORIES, PROPULSION_KINDS, VESSEL_TYPE_SPECS

    for name, spec in VESSEL_TYPE_SPECS.items():
        assert spec["propulsion"] in PROPULSION_KINDS, name
        assert spec["category"] in CATEGORIES, name
        assert spec["label"], name


def test_only_engineless_vessels_are_wind_dependent():
    from src.models.vessel_types import is_wind_dependent

    # Wind-assisted ships carry an engine, so they must not be hard-blocked.
    assert is_wind_dependent("tall_ship")
    assert is_wind_dependent("sailing_cargo_ship")
    assert not is_wind_dependent("wind_assisted_cargo_ship")
    assert not is_wind_dependent("container_ship")


def test_vessel_create_rejects_unknown_type():
    with pytest.raises(ValidationError):
        VesselCreateSchema(
            company_id="000000000000000000000001",
            name="Test Vessel",
            imo_number="IMO0000001",
            vessel_type="submarine",
        )


def test_vessel_update_accepts_missing_type():
    vessel = VesselUpdateSchema(name="Updated Vessel")
    assert vessel.vessel_type is None
