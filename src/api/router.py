from fastapi import APIRouter
from src.api.v1.routers import (
    analytics,
    auth,
    billing_data,
    companies,
    fleet,
    fleet_profiles,
    jobs,
    optimization,
    port_scheduling,
    routes,
    routing,
    vessels,
    zones,
    weather,
)
from src.api.v1.routers import ai as ai_router

router = APIRouter()
router.include_router(auth.router)
# Public reference data (ports, land mask) — registered so the map can draw
# before anyone logs in. See the comment on routes.public_router.
router.include_router(routes.public_router)
router.include_router(routes.router)
router.include_router(routing.router)
router.include_router(vessels.router)
router.include_router(fleet.router)
router.include_router(fleet_profiles.router)
router.include_router(companies.router)
router.include_router(billing_data.router)
router.include_router(zones.router)
router.include_router(ai_router.router)
router.include_router(weather.router)
router.include_router(analytics.router)
router.include_router(port_scheduling.router)
router.include_router(optimization.router)
router.include_router(jobs.router)
