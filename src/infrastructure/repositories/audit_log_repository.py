import mongoengine as me
from datetime import datetime
from src.models.audit_log import AuditLog

class AuditLogRepository:
    def create_log(
        self,
        event_type: str,
        data: dict,
        entity_type: str = None,
        entity_id=None,
        action: str = None,
        changed_by: str = None,
    ):
        """Write an audit entry.

        AuditLog has always carried entity_type/entity_id/action/changed_by,
        but this method did not accept them — so the dispatcher's attempt to
        pass entity_id raised TypeError and took the whole event with it.
        """
        log = AuditLog(
            event_type=event_type,
            data=data,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changed_by=changed_by,
        )
        log.save()
        return log

    def get_all_logs(self):
        return list(AuditLog.objects.order_by("-created_at"))

    def get_logs_by_type(self, event_type: str):
        return list(
            AuditLog.objects(event_type=event_type).order_by("-created_at")
        )

    def get_last_log(self):
        return AuditLog.objects.order_by("-created_at").first()
