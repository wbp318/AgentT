"""Tests for approval engine."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base, Entity, Transaction, ApprovalRequest,
    EntityType, AccountingMethod, TransactionType, QBSyncStatus,
    ApprovalStatus, ApprovalType,
)
from core.approval import ApprovalEngine
from core.events import EventBus, Event, APPROVAL_REQUESTED, APPROVAL_DECIDED


@pytest.fixture
def engine():
    """Create an in-memory database."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest.fixture
def seeded_session(session_factory):
    """Session with a test entity."""
    session = session_factory()
    entity = Entity(
        id=1,
        name="Test Farm",
        slug="test_farm",
        entity_type=EntityType.ROW_CROP_FARM,
        state="LA",
        accounting_method=AccountingMethod.CASH,
    )
    session.add(entity)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def approval_engine():
    """Create an ApprovalEngine with event bus."""
    engine = ApprovalEngine()
    event_bus = EventBus()
    engine.setup(event_bus)
    return engine


class TestCreateApproval:
    """Test approval creation."""

    def test_create_approval_returns_id(self, seeded_session, approval_engine):
        """Creating an approval should return an ID."""
        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_id = approval_engine.create_approval(
                    entity_id=1,
                    request_type=ApprovalType.QB_ENTRY,
                    action_description="Test approval",
                    data_payload={"test": True},
                )

        assert isinstance(approval_id, int)
        assert approval_id > 0

    def test_create_approval_emits_event(self, seeded_session, approval_engine):
        """Creating an approval should emit APPROVAL_REQUESTED."""
        emitted = []
        approval_engine._event_bus.subscribe(APPROVAL_REQUESTED, lambda e: emitted.append(e))

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_engine.create_approval(
                    entity_id=1,
                    request_type=ApprovalType.QB_ENTRY,
                    action_description="Test",
                    data_payload={},
                )

        assert len(emitted) == 1
        assert emitted[0].data["request_type"] == "qb_entry"

    def test_create_approval_pending_status(self, seeded_session, approval_engine):
        """New approvals should have PENDING status."""
        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_id = approval_engine.create_approval(
                    entity_id=1,
                    request_type="qb_entry",
                    action_description="Test",
                    data_payload={},
                )

        approval = seeded_session.get(ApprovalRequest, approval_id)
        assert approval.status == ApprovalStatus.PENDING


class TestDecideApproval:
    """Test approval decision making."""

    def _create_pending_approval(self, session):
        """Helper to create a pending approval."""
        approval = ApprovalRequest(
            entity_id=1,
            request_type=ApprovalType.QB_ENTRY,
            action_description="Test",
            data_payload={"test": True},
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        session.commit()
        return approval.id

    def test_approve_sets_status(self, seeded_session, approval_engine):
        """Approving should set status to APPROVED."""
        approval_id = self._create_pending_approval(seeded_session)

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_engine.decide(approval_id, "approved", decided_by="user")

        approval = seeded_session.get(ApprovalRequest, approval_id)
        assert approval.status == ApprovalStatus.APPROVED
        assert approval.decided_by == "user"
        assert approval.decided_at is not None

    def test_reject_sets_status(self, seeded_session, approval_engine):
        """Rejecting should set status to REJECTED."""
        approval_id = self._create_pending_approval(seeded_session)

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_engine.decide(approval_id, "rejected", decided_by="user", notes="Wrong vendor")

        approval = seeded_session.get(ApprovalRequest, approval_id)
        assert approval.status == ApprovalStatus.REJECTED
        assert approval.notes == "Wrong vendor"

    def test_decide_emits_event(self, seeded_session, approval_engine):
        """Deciding should emit APPROVAL_DECIDED."""
        approval_id = self._create_pending_approval(seeded_session)
        emitted = []
        approval_engine._event_bus.subscribe(APPROVAL_DECIDED, lambda e: emitted.append(e))

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_engine.decide(approval_id, "approved")

        assert len(emitted) == 1
        assert emitted[0].data["decision"] == "approved"

    def test_cannot_re_decide(self, seeded_session, approval_engine):
        """Cannot decide on an already decided approval."""
        approval_id = self._create_pending_approval(seeded_session)

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("core.approval.log_action"):
                approval_engine.decide(approval_id, "approved")

        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(ValueError, match="already decided"):
                approval_engine.decide(approval_id, "rejected")

    def test_decide_nonexistent_raises(self, seeded_session, approval_engine):
        """Deciding on a nonexistent approval should raise."""
        with patch("core.approval.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: seeded_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(ValueError, match="not found"):
                approval_engine.decide(9999, "approved")
