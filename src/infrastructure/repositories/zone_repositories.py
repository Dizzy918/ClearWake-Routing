from typing import Optional
from src.models.zone import Zone

class ZoneRepository:

    def create(self, zone: Zone) -> Zone:
        zone.save()
        return zone

    def get_by_id(self, zone_id: str) -> Optional[Zone]:
        return Zone.objects(id=zone_id).first()

    def get_all(self) -> list[Zone]:
        return list(Zone.objects.all())

    def get_active(self) -> list[Zone]:
        return list(Zone.objects(status="active"))

    # -- tenant-scoped reads --
    # A company sees the shared zones (company_id unset — official closures and
    # imported hazards) plus its own. It never sees another operator's.
    def visible_to(self, company_id) -> list[Zone]:
        from mongoengine.queryset.visitor import Q

        return list(Zone.objects(Q(company_id=None) | Q(company_id=company_id)))

    def active_visible_to(self, company_id) -> list[Zone]:
        from mongoengine.queryset.visitor import Q

        return list(
            Zone.objects(Q(company_id=None) | Q(company_id=company_id)) .filter(status="active")
        )

    def is_editable_by(self, zone: Zone, user) -> bool:
        """Shared zones are admin-only; a company's own zones are its own."""
        if zone.company_id is None:
            return getattr(user, "role", None) == "admin"
        return str(zone.company_id) == str(getattr(user, "company_id", ""))

    def get_by_type(self, zone_type: str) -> list[Zone]:
        return list(Zone.objects(zone_type=zone_type))

    def update(self, zone_id: str, data: dict) -> Optional[Zone]:
        zone = self.get_by_id(zone_id)
        if not zone:
            return None
        zone.update(**data)
        zone.reload()
        return zone

    def delete(self, zone_id: str) -> bool:
        zone = self.get_by_id(zone_id)
        if not zone:
            return False
        zone.delete()
        return True

    def activate(self, zone_id: str) -> Optional[Zone]:
        return self.update(zone_id, {"status": "active"})

    def deactivate(self, zone_id: str) -> Optional[Zone]:
        return self.update(zone_id, {"status": "inactive"})
