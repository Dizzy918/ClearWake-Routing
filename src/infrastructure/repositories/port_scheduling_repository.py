"""Data access for port scheduling models."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from bson import ObjectId

from src.models.port_scheduling import DockReservation, Port, PortSchedule
from src.core.time_utils import utc_now


class PortRepository:
    def create(self, data: dict) -> Port:
        port = Port(**data).save()
        return port

    def get_by_port_id(self, port_id: str) -> Optional[Port]:
        return Port.objects(port_id=port_id).first()

    def list_all(self, limit: int = 500) -> List[Port]:
        return list(Port.objects().limit(limit))

    def delete(self, port_id: str) -> bool:
        result = Port.objects(port_id=port_id).delete()
        return bool(result)


class PortScheduleRepository:
    def upsert(self, port_id: str, data: dict) -> PortSchedule:
        schedule = PortSchedule.objects(port_id=port_id).first()
        if schedule is None:
            schedule = PortSchedule(port_id=port_id, **data)
        else:
            for key, value in data.items():
                setattr(schedule, key, value)
        schedule.updated_at = utc_now()
        schedule.save()
        return schedule

    def get(self, port_id: str) -> Optional[PortSchedule]:
        return PortSchedule.objects(port_id=port_id).first()


class DockReservationRepository:
    def create(self, data: dict) -> DockReservation:
        reservation = DockReservation(**data)
        reservation.clean()
        reservation.save()
        return reservation

    def get_by_id(self, reservation_id: str) -> Optional[DockReservation]:
        if not ObjectId.is_valid(reservation_id):
            return None
        return DockReservation.objects(id=reservation_id).first()

    def list_for_port(
        self,
        port_id: str,
        berth_number: Optional[int] = None,
        starting_after: Optional[datetime] = None,
        limit: int = 200,
    ) -> List[DockReservation]:
        query = {"port_id": port_id}
        if berth_number is not None:
            query["berth_number"] = berth_number
        if starting_after is not None:
            query["end_at__gt"] = starting_after
        return list(
            DockReservation.objects(**query)
            .order_by("start_at")
            .limit(limit)
        )

    def find_conflicts(self, candidate: DockReservation) -> List[DockReservation]:
        """Return existing reservations overlapping with *candidate*."""
        existing = self.list_for_port(
            candidate.port_id,
            berth_number=candidate.berth_number,
            starting_after=candidate.start_at,
        )
        return [r for r in existing if candidate.conflicts_with(r) and r.id != candidate.id]

    def cancel(self, reservation_id: str) -> Optional[DockReservation]:
        reservation = self.get_by_id(reservation_id)
        if reservation is None:
            return None
        reservation.status = "cancelled"
        reservation.save()
        return reservation
