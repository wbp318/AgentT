"""
Audit logging for AgentT.
Records all agent actions to both the database and a log file.
"""

import logging
from datetime import datetime

from database.db import get_session
from database.models import AuditLog, AuditSeverity
from config.settings import LOG_DIR

# Set up file-based audit logger (append-only)
audit_file_logger = logging.getLogger("audit")
audit_file_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_DIR / "audit.log", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
audit_file_logger.addHandler(_handler)


def log_action(
    module: str,
    action: str,
    detail: dict = None,
    entity_id: int = None,
    user: str = "system",
    severity: str = "info",
):
    """
    Record an audit event to both database and log file.

    Args:
        module: Which module performed the action (e.g., "scanner", "quickbooks")
        action: What happened (e.g., "document_scanned", "iif_generated")
        detail: Additional context as a dict
        entity_id: Which entity this relates to (optional)
        user: Who triggered this ("system" for autonomous actions)
        severity: "info", "warning", or "error"
    """
    sev = AuditSeverity(severity)

    # Write to database
    try:
        with get_session() as session:
            entry = AuditLog(
                timestamp=datetime.utcnow(),
                entity_id=entity_id,
                module=module,
                action=action,
                detail=detail,
                user=user,
                severity=sev,
            )
            session.add(entry)
    except Exception as e:
        audit_file_logger.error(f"Failed to write audit to DB: {e}")

    # Always write to log file (redundancy)
    msg = f"[{severity.upper()}] [{module}] {action}"
    if entity_id:
        msg += f" (entity={entity_id})"
    if detail:
        msg += f" | {detail}"
    audit_file_logger.info(msg)
