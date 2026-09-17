from sqlalchemy.orm import Session

from app.models import Admin, AuditLog


def log_activity(
    db: Session,
    admin: Admin,
    action: str,
    description: str,
    related_type: str | None = None,
    related_id: int | None = None,
) -> None:
    """Records one audit trail entry. Does not commit — callers already
    commit the surrounding change, so this rides along in the same
    transaction (an activity log entry should never exist for a change that
    didn't actually happen)."""
    db.add(
        AuditLog(
            admin_id=admin.id,
            admin_name=admin.name,
            action=action,
            description=description[:500],
            related_type=related_type,
            related_id=related_id,
        )
    )
