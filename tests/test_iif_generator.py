"""Tests for IIF file generation."""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from database.db import init_db, get_session
from database.models import (
    Base, Entity, Transaction, TransactionType, IIFType, QBSyncStatus,
    EntityType, AccountingMethod,
)
from modules.quickbooks.iif_generator import IIFGenerator
from core.events import EventBus, Event, APPROVAL_DECIDED


@pytest.fixture
def db_session():
    """Create an in-memory database with test data."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed test entity
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
def iif_generator():
    """Create an IIFGenerator with a mock event bus."""
    gen = IIFGenerator()
    event_bus = EventBus()
    gen.setup(event_bus)
    return gen


def _create_transaction(session, **overrides):
    """Helper to create a test transaction."""
    defaults = {
        "entity_id": 1,
        "transaction_type": TransactionType.EXPENSE,
        "iif_type": IIFType.BILL,
        "date": date(2026, 1, 15),
        "vendor_customer": "Helena Chemical",
        "amount": 1234.56,
        "category": "chemicals",
        "qb_account": "Chemicals",
        "reference_number": "INV-001",
        "description": "Chemical purchase",
        "qb_sync_status": QBSyncStatus.PENDING,
    }
    defaults.update(overrides)
    txn = Transaction(**defaults)
    session.add(txn)
    session.commit()
    return txn


class TestIIFFormat:
    """Test IIF format compliance."""

    def test_bill_format_has_tab_separation(self, db_session, iif_generator):
        """IIF lines must be tab-separated."""
        txn = _create_transaction(db_session, iif_type=IIFType.BILL)

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        for line in content.split("\r\n"):
            if line.strip() and line not in ("ENDTRNS", "!ENDTRNS"):
                assert "\t" in line, f"Line missing tabs: {line}"

    def test_bill_format_has_crlf_endings(self, db_session, iif_generator):
        """IIF must use CRLF line endings."""
        txn = _create_transaction(db_session, iif_type=IIFType.BILL)

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        assert "\r\n" in content, "IIF must use CRLF line endings"

    def test_bill_amounts_balance(self, db_session, iif_generator):
        """TRNS + SPL amounts must sum to zero."""
        txn = _create_transaction(db_session, amount=500.00, iif_type=IIFType.BILL)

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        total = 0.0
        for line in content.split("\r\n"):
            if line.startswith("TRNS\t") or line.startswith("SPL\t"):
                fields = line.split("\t")
                amount_str = fields[6]  # AMOUNT is field index 6
                total += float(amount_str)

        assert abs(total) < 0.01, f"Amounts don't balance: {total}"

    def test_bill_date_format(self, db_session, iif_generator):
        """Dates must be MM/DD/YYYY format."""
        txn = _create_transaction(
            db_session, date=date(2026, 3, 7), iif_type=IIFType.BILL
        )

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        for line in content.split("\r\n"):
            if line.startswith("TRNS\t"):
                fields = line.split("\t")
                assert fields[3] == "03/07/2026", f"Wrong date format: {fields[3]}"


class TestIIFTypes:
    """Test each IIF transaction type produces correct structure."""

    def test_bill_structure(self, db_session, iif_generator):
        """BILL: TRNS on AP (negative), SPL on expense (positive)."""
        txn = _create_transaction(
            db_session, iif_type=IIFType.BILL, amount=100.00
        )

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        lines = [l for l in content.split("\r\n") if l.startswith(("TRNS\t", "SPL\t"))]
        assert len(lines) == 2

        trns_fields = lines[0].split("\t")
        spl_fields = lines[1].split("\t")

        assert trns_fields[2] == "BILL"
        assert trns_fields[4] == "Accounts Payable"
        assert float(trns_fields[6]) == -100.00

        assert spl_fields[2] == "BILL"
        assert spl_fields[4] == "Chemicals"
        assert float(spl_fields[6]) == 100.00

    def test_check_structure(self, db_session, iif_generator):
        """CHECK: TRNS on Checking (negative), SPL on expense (positive)."""
        txn = _create_transaction(
            db_session, iif_type=IIFType.CHECK, amount=250.00
        )

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        lines = [l for l in content.split("\r\n") if l.startswith(("TRNS\t", "SPL\t"))]
        trns_fields = lines[0].split("\t")
        spl_fields = lines[1].split("\t")

        assert trns_fields[2] == "CHECK"
        assert trns_fields[4] == "Checking"
        assert float(trns_fields[6]) == -250.00

        assert spl_fields[4] == "Chemicals"
        assert float(spl_fields[6]) == 250.00

    def test_deposit_structure(self, db_session, iif_generator):
        """DEPOSIT: TRNS on Checking (positive), SPL on income (negative)."""
        txn = _create_transaction(
            db_session,
            iif_type=IIFType.DEPOSIT,
            transaction_type=TransactionType.INCOME,
            amount=5000.00,
            qb_account="Grain Sales",
            vendor_customer="ADM",
        )

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        lines = [l for l in content.split("\r\n") if l.startswith(("TRNS\t", "SPL\t"))]
        trns_fields = lines[0].split("\t")
        spl_fields = lines[1].split("\t")

        assert trns_fields[2] == "DEPOSIT"
        assert trns_fields[4] == "Checking"
        assert float(trns_fields[6]) == 5000.00

        assert spl_fields[4] == "Grain Sales"
        assert float(spl_fields[6]) == -5000.00

    def test_header_rows(self, db_session, iif_generator):
        """IIF must start with !TRNS, !SPL, !ENDTRNS header rows."""
        txn = _create_transaction(db_session)

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        lines = content.split("\r\n")
        assert lines[0].startswith("!TRNS")
        assert lines[1].startswith("!SPL")
        assert lines[2] == "!ENDTRNS"

    def test_endtrns_present(self, db_session, iif_generator):
        """Each transaction block must end with ENDTRNS."""
        txn = _create_transaction(db_session)

        with patch("modules.quickbooks.iif_generator.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = lambda s: db_session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            content = iif_generator.preview_iif(txn.id)

        assert "ENDTRNS" in content


class TestApprovalHandler:
    """Test IIF generation triggered by approval events."""

    def test_approved_triggers_generation(self, db_session, iif_generator):
        """Approving a QB entry should trigger IIF generation."""
        txn = _create_transaction(db_session)

        with patch.object(iif_generator, "generate_iif") as mock_gen:
            event = Event(APPROVAL_DECIDED, {
                "decision": "approved",
                "transaction_id": txn.id,
            })
            iif_generator._handle_approval_decided(event)
            mock_gen.assert_called_once_with(txn.id)

    def test_rejected_does_not_trigger(self, iif_generator):
        """Rejecting should NOT trigger IIF generation."""
        with patch.object(iif_generator, "generate_iif") as mock_gen:
            event = Event(APPROVAL_DECIDED, {
                "decision": "rejected",
                "transaction_id": 1,
            })
            iif_generator._handle_approval_decided(event)
            mock_gen.assert_not_called()

    def test_no_transaction_id_skips(self, iif_generator):
        """Missing transaction_id should skip generation."""
        with patch.object(iif_generator, "generate_iif") as mock_gen:
            event = Event(APPROVAL_DECIDED, {
                "decision": "approved",
            })
            iif_generator._handle_approval_decided(event)
            mock_gen.assert_not_called()
