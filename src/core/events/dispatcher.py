import asyncio
import logging

from bson import ObjectId
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from src.infrastructure.repositories.audit_log_repository import AuditLogRepository

logger = logging.getLogger(__name__)


class EventDispatcher:
    def __init__(self):
        self._subscribers = defaultdict(list)
        self._lock = Lock()
        self._audit_repo = AuditLogRepository()
        self._executor = ThreadPoolExecutor(max_workers=5)
        self._ws_manager = None
        self._loop = None

    def set_ws_manager(self, ws_manager, loop=None):
        """Attach a WebSocketManager for real-time event broadcasting.

        The loop must be captured here, at startup, because dispatch() is
        normally called from a FastAPI threadpool worker — a sync endpoint
        handler. asyncio.get_event_loop() in that thread does not return the
        server's loop, so without this every broadcast raised RuntimeError
        and was silently swallowed.
        """
        self._ws_manager = ws_manager
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        self._loop = loop

    def _emit(self, coro_factory):
        """Run a coroutine on the server loop from whichever thread we are on."""
        if not self._ws_manager:
            return
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            return
        try:
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        except RuntimeError:  # loop shutting down
            logger.debug("dropped a websocket broadcast: loop unavailable")

    def broadcast_to_company(self, company_id: str, message: dict):
        """Push a message to one company's dashboards, from sync code."""
        if not company_id:
            return
        self._emit(lambda: self._ws_manager.send_to_company(str(company_id), message))

    def subscribe(self, event_type: str, observer):
        with self._lock:
            if observer not in self._subscribers[event_type]:
                self._subscribers[event_type].append(observer)

    def unsubscribe(self, event_type: str, observer):
        with self._lock:
            if observer in self._subscribers[event_type]:
                self._subscribers[event_type].remove(observer)

    @staticmethod
    def _entity_of(event):
        """Which record an event is about, for audit filtering."""
        for key, entity_type in (
            ("zone_id", "zone"),
            ("vessel_id", "vessel"),
            ("route_id", "route"),
        ):
            value = event.data.get(key)
            if value:
                try:
                    return entity_type, ObjectId(str(value))
                except Exception:
                    return entity_type, None
        return "event", None

    def dispatch(self, event):
        observers = self._subscribers.get(event.event_type, [])

        # One entry per event. This used to log twice — once positionally and
        # once with keywords — and the second call raised TypeError, so no
        # observer ever ran and nothing was broadcast.
        entity_type, entity_id = self._entity_of(event)
        try:
            self._audit_repo.create_log(
                event_type=event.event_type,
                data=event.data,
                entity_type=entity_type,
                entity_id=entity_id,
                changed_by=event.data.get("changed_by"),
            )
        except Exception:
            # An audit failure must not swallow the event itself.
            logger.exception("could not write audit log for %s", event.event_type)

        for observer in observers:
            self._executor.submit(observer.update, event)

        company_id = event.data.get("company_id")
        message = {"event_type": event.event_type, "payload": event.data}
        if company_id:
            # Fleet events belong to one operator; do not fan them out to
            # every connected dashboard.
            self.broadcast_to_company(company_id, message)
        else:
            self._emit(lambda: self._ws_manager.broadcast(message))

dispatcher = EventDispatcher()
