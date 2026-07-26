import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.errors import APIError
from src.api.router import router as api_router
from src.api.v1.routers.ai import ws_notifications, ws_manager
from src.core.config import settings
from src.core.events.ai_observer import register_ai_observer
from src.core.events.dispatcher import dispatcher
from src.infrastructure.database.database import close_db, init_db
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)
logging.basicConfig(level=settings.LOG_LEVEL.upper())

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    register_ai_observer()
    dispatcher.set_ws_manager(ws_manager)
    logger.info("AI module initialised (observer + WebSocket manager)")
    yield
    close_db()
    from src.api.v1.routers.weather import _close_client
    await _close_client()

app = FastAPI(title="ClearWake Routing", lifespan=lifespan)

# The two biggest responses are text: map.html (~128 kB) and the land mask
# GeoJSON (~591 kB). Both compress by roughly 4-5x, and both are on the
# critical path for the map rendering at all.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
app.add_api_websocket_route("/ws/ai/notifications", ws_notifications)

Instrumentator().instrument(app).expose(app)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")

@app.get("/map", response_class=HTMLResponse)
async def map_view():
    try:
        with open("src/static/map.html", "r", encoding="utf-8") as f:
            html = f.read()

        rendered = html.replace("__MAP_PROVIDER__", settings.MAP_PROVIDER)
        return rendered

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="The file 'src/static/map.html' isn't found. Make sure you have started uvicorn from the main folder of the project."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Server error while reading: {str(e)}"
        )

@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    logger.error(f"API Error: {exc.message} - {exc.details}")

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "details": exc.details
            }
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error: {str(exc)}")

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal Server Error",
                "details": str(exc)
            }
        }
    )
