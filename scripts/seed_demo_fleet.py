"""Seed a demo operator: one company, three sub-accounts, and a fleet.

Gives a working system to log into instead of an empty map. Safe to re-run —
everything is upserted by a natural key.

    venv/bin/python scripts/seed_demo_fleet.py

The admin password is read from SEED_ADMIN_PASSWORD; if unset a random one is
generated and printed once. Nothing is hard-coded, so committing this file
does not commit a credential.
"""

from __future__ import annotations

import os
import random
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.security import hash_password  # noqa: E402
from src.core.time_utils import utc_now  # noqa: E402
from src.infrastructure.database.database import init_db  # noqa: E402
from src.models.company import Company  # noqa: E402
from src.models.user import User  # noqa: E402
from src.models.vessel import Vessel  # noqa: E402
from src.models.vessel_types import VESSEL_TYPE_SPECS  # noqa: E402

COMPANY_NAME = "Meridian Shipping"
COMPANY_EMAIL = "ops@meridian-demo.example"

# Sailing types are included deliberately: they are what exercise the
# weather rules, since severe conditions block them outright.
FLEET_PLAN = [
    ("MV Aurora Borealis", "container_ship", 14.5, 366, 51),
    ("MV Northern Star", "ultra_large_container_ship", 16.0, 400, 59),
    ("MT Baltic Trader", "crude_oil_tanker", 17.5, 333, 60),
    ("MT Aegean Spirit", "product_tanker", 12.8, 183, 32),
    ("MT Caspian Pearl", "chemical_tanker", 11.2, 145, 24),
    ("LNG Polaris", "lng_carrier", 12.5, 290, 45),
    ("LPG Meridian", "lpg_carrier", 11.8, 230, 36),
    ("MV Iron Duke", "bulk_carrier", 18.2, 292, 45),
    ("MV Ore Titan", "ore_carrier", 23.0, 340, 62),
    ("MV Cement Runner", "cement_carrier", 9.5, 130, 20),
    ("MV Adriatic Trader", "general_cargo", 9.8, 140, 21),
    ("MV Heavy Hauler", "heavy_lift_vessel", 10.5, 160, 30),
    ("MV Cold Chain", "reefer", 10.2, 155, 24),
    ("MV Vehicle Voyager", "car_carrier", 11.0, 200, 32),
    ("MV Channel Bridge", "ro_ro_ship", 8.5, 180, 28),
    ("MS Coastal Princess", "cruise_ship", 8.2, 300, 38),
    ("MS Island Link", "ropax", 6.8, 160, 26),
    ("MS Harbour Shuttle", "ferry", 5.5, 90, 18),
    ("MV Deep Support", "platform_supply_vessel", 6.5, 85, 20),
    ("MV Anchor Master", "anchor_handling_tug", 7.2, 80, 18),
    ("MV Windfarm Runner", "wind_farm_service_vessel", 3.2, 35, 11),
    ("MV Polar Pioneer", "icebreaker", 11.0, 160, 28),
    ("CS Cable Weaver", "cable_layer", 7.8, 140, 24),
    ("MV Dredge Master", "dredger", 8.0, 120, 22),
    ("RV Ocean Scholar", "research_vessel", 6.2, 95, 18),
    ("SV Sea Surveyor", "survey_vessel", 5.4, 70, 15),
    ("FV Atlantic Harvest", "trawler", 6.0, 75, 14),
    ("FV Pacific Seiner", "purse_seiner", 5.8, 68, 13),
    ("TG Harbour Hercules", "tugboat", 5.5, 32, 12),
    ("TG Salvage Chief", "salvage_tug", 6.5, 70, 16),
    ("PB Coastal Watch", "patrol_boat", 3.5, 55, 9),
    ("BT Light Keeper", "buoy_tender", 4.2, 60, 12),
    ("MT Bunker Servant", "bunkering_tanker", 6.8, 100, 17),
    # --- wind powered: blocked by severe weather ---
    ("SV Windward Trader", "sailing_cargo_ship", 4.5, 60, 11),
    ("SV Sea Lark", "sailing_yacht", 2.8, 24, 6),
    ("STS Grand Voyager", "tall_ship", 5.2, 90, 13),
    ("STS Young Mariner", "sail_training_ship", 4.0, 55, 10),
    ("MV Aeolus Pioneer", "wind_assisted_cargo_ship", 9.5, 160, 24),
]

# Scattered across trading areas so the fleet view is not one dot.
SEED_POSITIONS = [
    (27.9, 43.2), (28.9, 41.0), (23.6, 37.9), (18.1, 40.6), (12.3, 45.6),
    (5.3, 43.3), (2.2, 41.3), (-0.1, 38.3), (-5.4, 36.1), (-9.1, 38.7),
    (-1.4, 50.8), (4.4, 51.9), (8.5, 53.5), (12.6, 55.7), (18.1, 59.3),
    (24.9, 60.2), (10.7, 59.9), (-4.1, 50.4), (-8.6, 51.9), (-22.0, 64.1),
    (32.3, 31.2), (34.8, 32.1), (43.2, 12.8), (56.3, 26.2), (72.8, 18.9),
    (80.3, 6.9), (103.8, 1.3), (114.2, 22.3), (121.5, 31.2), (139.8, 35.4),
    (-74.0, 40.7), (-80.2, 25.8), (-118.3, 33.7), (-123.1, 49.3), (-43.2, -22.9),
    (18.4, -33.9), (151.2, -33.9), (174.8, -36.8),
]


def upsert_company() -> Company:
    company = Company.objects(email=COMPANY_EMAIL).first()
    if company:
        return company
    company = Company(name=COMPANY_NAME, email=COMPANY_EMAIL, status="active")
    company.save()
    return company


def upsert_user(company, email, name, role, password) -> tuple[User, bool]:
    user = User.objects(email=email).first()
    if user:
        return user, False
    user = User(
        company_id=company.id,
        email=email,
        password_hash=hash_password(password),
        full_name=name,
        role=role,
        is_active=True,
        created_at=utc_now(),
    )
    user.save()
    return user, True


def upsert_fleet(company) -> tuple[int, int]:
    created = existing = 0
    rng = random.Random(20260726)  # stable across runs
    for index, (name, vessel_type, draft, length, beam) in enumerate(FLEET_PLAN):
        imo = f"IMO{9000000 + index}"
        vessel = Vessel.objects(imo_number=imo).first()
        if vessel:
            existing += 1
            continue
        lon, lat = SEED_POSITIONS[index % len(SEED_POSITIONS)]
        Vessel(
            company_id=company.id,
            name=name,
            imo_number=imo,
            vessel_type=vessel_type,
            specs={
                "max_draft_m": draft,
                "max_speed_knots": round(rng.uniform(10, 22), 1),
                "length_m": length,
                "beam_m": beam,
                "max_cargo_t": round(length * beam * 4.2),
                "cargo_weight_t": round(length * beam * 4.2 * rng.uniform(0.3, 0.9)),
                "trim_m": round(rng.uniform(-0.5, 1.5), 2),
                "hydro_resistance_coef": round(rng.uniform(0.95, 1.15), 3),
            },
            fuel_consumption_rate=round(rng.uniform(0.02, 0.09), 4),
            current_status=rng.choice(["idle", "en_route", "docked", "anchored"]),
            current_position={"type": "Point", "coordinates": [lon, lat]},
            heading_deg=round(rng.uniform(0, 359), 1),
            speed_knots=round(rng.uniform(0, 18), 1),
            position_updated_at=utc_now(),
        ).save()
        created += 1
    return created, existing


def main() -> None:
    init_db()
    company = upsert_company()

    password = os.environ.get("SEED_ADMIN_PASSWORD")
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(12)

    accounts = [
        ("admin@meridian-demo.example", "Fleet Director", "admin"),
        ("operator@meridian-demo.example", "Duty Operator", "operator"),
        ("viewer@meridian-demo.example", "Charterer (read-only)", "viewer"),
    ]
    made = []
    for email, name, role in accounts:
        _, is_new = upsert_user(company, email, name, role, password)
        made.append((email, role, is_new))

    created, existing = upsert_fleet(company)

    print(f"company: {company.name} ({company.id})")
    for email, role, is_new in made:
        print(f"  {'created' if is_new else 'exists '} {role:9} {email}")
    print(f"fleet: {created} created, {existing} already present")
    print(f"vessel types available: {len(VESSEL_TYPE_SPECS)}")
    if generated and any(is_new for _, _, is_new in made):
        print(f"\n  password for all three accounts: {password}")
        print("  (set SEED_ADMIN_PASSWORD to choose your own; this is shown once)")
    elif not generated:
        print("\n  password taken from SEED_ADMIN_PASSWORD")
    else:
        print("\n  accounts already existed — password unchanged")


if __name__ == "__main__":
    main()
