"""
Approval engine for AgentT.
Manages the approval lifecycle for sensitive operations (QB entries, invoices, etc.).
"""

import logging
from datetime import datetime

from core.events import EventBus, Event, APPROVAL_REQUESTED, APPROVAL_DECIDED
from core.audit import log_action
from database.db import get_session
from database.models import ApprovalRequest, ApprovalStatus, ApprovalType

logger = logging.getLogger(__name__)


class ApprovalEngine:
    """Manages approval lifecycle, emits events."""

    def setup(self, event_bus):
        self._event_bus = event_bus

    def create_approval(self, entity_id, request_type, action_description,
                        data_payload, transaction_id=None):
        """Create a new approval request.

        Args:
            entity_id: Which entity this relates to
            request_type: ApprovalType enum value or string
            action_description: Human-readable description of what's being approved
            data_payload: Dict with details for display/processing
            transaction_id: Optional linked transaction ID

        Returns:
            approval_id (int)
        """
        if isinstance(request_type, str):
            request_type = ApprovalType(request_type)

        with get_session() as session:
            approval = ApprovalRequest(
                entity_id=entity_id,
                request_type=request_type,
                action_description=action_description,
                data_payload=data_payload,
                status=ApprovalStatus.PENDING,
            )
            session.add(approval)
            session.flush()  # Get the ID before commit
            approval_id = approval.id

            # Link transaction if provided
            if transaction_id:
                from database.models import Transaction
                txn = session.get(Transaction, transaction_id)
                if txn:
                    txn.approval_id = approval_id

        log_action(
            "approval",
            "approval_requested",
            detail={
                "approval_id": approval_id,
                "request_type": request_type.value,
                "action": action_description,
                "transaction_id": transaction_id,
            },
            entity_id=entity_id,
        )

        if self._event_bus:
            self._event_bus.emit(Event(APPROVAL_REQUESTED, {
                "approval_id": approval_id,
                "request_type": request_type.value,
                "entity_id": entity_id,
                "transaction_id": transaction_id,
            }))

        logger.info(f"Approval #{approval_id} created: {action_description}")
        return approval_id

    def decide(self, approval_id, decision, decided_by="user", notes=""):
        """Approve or reject an approval request.

        Args:
            approval_id: ID of the approval to decide
            decision: "approved" or "rejected"
            decided_by: Who made the decision
            notes: Optional notes

        Returns:
            The updated ApprovalRequest

        Raises:
            ValueError: If approval not found or already decided
        """
        with get_session() as session:
            approval = session.get(ApprovalRequest, approval_id)
            if not approval:
                raise ValueError(f"Approval #{approval_id} not found")

            if approval.status != ApprovalStatus.PENDING:
                raise ValueError(
                    f"Approval #{approval_id} already decided ({approval.status.value})"
                )

            approval.status = ApprovalStatus(decision)
            approval.decided_at = datetime.utcnow()
            approval.decided_by = decided_by
            approval.notes = notes

            # Capture data for event emission after commit
            event_data = {
                "approval_id": approval_id,
                "decision": decision,
                "entity_id": approval.entity_id,
                "request_type": approval.request_type.value,
                "data_payload": approval.data_payload,
            }

            # Find linked transaction
            transaction_id = None
            if approval.transactions:
                transaction_id = approval.transactions[0].id
            event_data["transaction_id"] = transaction_id

        log_action(
            "approval",
            f"approval_{decision}",
            detail={
                "approval_id": approval_id,
                "decided_by": decided_by,
                "notes": notes,
                "transaction_id": transaction_id,
            },
            entity_id=event_data["entity_id"],
        )

        if self._event_bus:
            self._event_bus.emit(Event(APPROVAL_DECIDED, event_data))

        logger.info(f"Approval #{approval_id} {decision} by {decided_by}")
        return approval

    def get_pending(self, entity_id=None):
        """Get all pending approval requests.

        Args:
            entity_id: Optional filter by entity

        Returns:
            List of ApprovalRequest objects
        """
        with get_session() as session:
            query = session.query(ApprovalRequest).filter(
                ApprovalRequest.status == ApprovalStatus.PENDING
            )
            if entity_id:
                query = query.filter(ApprovalRequest.entity_id == entity_id)
            results = query.order_by(ApprovalRequest.requested_at.desc()).all()
            # Detach from session so they can be used after session closes
            session.expunge_all()
            return results
