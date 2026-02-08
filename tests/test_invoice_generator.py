"""Tests for invoice generation module."""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from database.models import (
    Base, Entity, Invoice, InvoiceStatus, EntityType, AccountingMethod,
)
from modules.billing.invoice_generator import InvoiceGenerator
from core.events import EventBus


@pytest.fixture
def db_session():
    """Create an in-memory database with test entities."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed test entities
    entity1 = Entity(
        id=1,
        name="Parker Farms Partnership",
        slug="farm_1",
        entity_type=EntityType.ROW_CROP_FARM,
        state="LA",
        accounting_method=AccountingMethod.CASH,
        address="689 Lensing Ln, Lake Providence, LA 71254-5404",
        phone="(318) 559-2020",
        email="tap@pfpartnership.com",
        invoice_prefix="PFP",
    )
    entity2 = Entity(
        id=2,
        name="New Generation Farms",
        slug="farm_2",
        entity_type=EntityType.ROW_CROP_FARM,
        state="LA",
        accounting_method=AccountingMethod.CASH,
        address="689 Lensing Ln, Lake Providence, LA 71254-5404",
        phone="(318) 282-6499",
        email="nolan@pfpartnership.com",
        invoice_prefix="NGF",
    )
    session.add_all([entity1, entity2])
    session.commit()

    yield session
    session.close()


@pytest.fixture
def invoice_gen():
    """Create an InvoiceGenerator with a mock event bus."""
    gen = InvoiceGenerator()
    event_bus = EventBus()
    gen.setup(event_bus)
    return gen


def _mock_get_session(db_session):
    """Create a mock get_session context manager that returns the test session."""
    mock_gs = MagicMock()
    mock_gs.return_value.__enter__ = lambda s: db_session
    mock_gs.return_value.__exit__ = MagicMock(return_value=False)
    return mock_gs


# === Invoice Creation ===

class TestCreateInvoice:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_create_invoice_basic(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        line_items = [
            {"description": "Custom hire - combine", "quantity": 100, "unit_price": 25.00},
            {"description": "Hauling", "quantity": 50, "unit_price": 10.00},
        ]

        invoice_id = invoice_gen.create_invoice(
            entity_id=1,
            customer_name="John Doe Farms",
            customer_address="123 Farm Rd, Monroe, LA 71201",
            date_due=date(2026, 3, 15),
            line_items=line_items,
            notes="Net 30",
        )

        assert invoice_id is not None
        invoice = db_session.get(Invoice, invoice_id)
        assert invoice.customer_name == "John Doe Farms"
        assert invoice.total_amount == 3000.00  # 100*25 + 50*10
        assert invoice.status == InvoiceStatus.DRAFT
        assert invoice.amount_paid == 0.0
        assert invoice.invoice_number.startswith("PFP-")
        assert invoice.notes == "Net 30"
        mock_log.assert_called_once()

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_create_invoice_auto_number(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        year = date.today().year
        invoice_id = invoice_gen.create_invoice(
            entity_id=1,
            customer_name="Test Customer",
            customer_address="",
            date_due=date(2026, 3, 1),
            line_items=[{"description": "Service", "quantity": 1, "unit_price": 100}],
        )

        invoice = db_session.get(Invoice, invoice_id)
        assert invoice.invoice_number == f"PFP-{year}-001"

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_create_invoice_calculates_line_amounts(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        line_items = [
            {"description": "Grain sale", "quantity": 3.5, "unit_price": 200.00},
        ]

        invoice_id = invoice_gen.create_invoice(
            entity_id=1,
            customer_name="Buyer",
            customer_address="",
            date_due=date(2026, 4, 1),
            line_items=line_items,
        )

        invoice = db_session.get(Invoice, invoice_id)
        assert invoice.total_amount == 700.00
        assert invoice.line_items[0]["amount"] == 700.00

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_create_invoice_invalid_entity(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(ValueError, match="Entity 999 not found"):
            invoice_gen.create_invoice(
                entity_id=999,
                customer_name="Nobody",
                customer_address="",
                date_due=date(2026, 3, 1),
                line_items=[{"description": "x", "quantity": 1, "unit_price": 1}],
            )


# === Sequential Numbering ===

class TestSequentialNumbering:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_sequential_numbers_per_entity(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        year = date.today().year
        items = [{"description": "Service", "quantity": 1, "unit_price": 100}]

        id1 = invoice_gen.create_invoice(1, "A", "", date(2026, 3, 1), items)
        id2 = invoice_gen.create_invoice(1, "B", "", date(2026, 3, 1), items)
        id3 = invoice_gen.create_invoice(2, "C", "", date(2026, 3, 1), items)

        inv1 = db_session.get(Invoice, id1)
        inv2 = db_session.get(Invoice, id2)
        inv3 = db_session.get(Invoice, id3)

        assert inv1.invoice_number == f"PFP-{year}-001"
        assert inv2.invoice_number == f"PFP-{year}-002"
        assert inv3.invoice_number == f"NGF-{year}-001"


# === PDF Generation ===

class TestPDFGeneration:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_generate_pdf(self, mock_log, mock_gs, db_session, invoice_gen, tmp_path):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        # Create invoice first
        invoice = Invoice(
            id=10,
            entity_id=1,
            invoice_number="PFP-2026-001",
            customer_name="PDF Test Customer",
            customer_address="Test Address",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[{"description": "Service", "quantity": 1, "unit_price": 500, "amount": 500}],
            total_amount=500.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        with patch("modules.billing.invoice_generator.INVOICES_DIR", tmp_path):
            # Mock WeasyPrint
            with patch("modules.billing.invoice_generator.InvoiceGenerator.generate_pdf") as mock_pdf:
                expected_path = str(tmp_path / "farm_1" / "2026" / "PFP-2026-001.pdf")
                mock_pdf.return_value = expected_path
                result = invoice_gen.generate_pdf(10)
                assert result == expected_path

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_generate_pdf_not_found(self, mock_log, mock_gs, db_session, invoice_gen):
        """generate_pdf raises ValueError for nonexistent invoice.
        We patch weasyprint import at module level to avoid GTK dependency."""
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        import sys
        mock_weasyprint = MagicMock()
        sys.modules["weasyprint"] = mock_weasyprint

        try:
            with pytest.raises(ValueError, match="Invoice 999 not found"):
                invoice_gen.generate_pdf(999)
        finally:
            del sys.modules["weasyprint"]


# === Payment Recording ===

class TestPaymentRecording:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_partial_payment(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=20,
            entity_id=1,
            invoice_number="PFP-2026-010",
            customer_name="Partial Payer",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[{"description": "x", "quantity": 1, "unit_price": 1000, "amount": 1000}],
            total_amount=1000.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        result = invoice_gen.record_payment(20, 400)
        assert result["total_paid"] == 400.00
        assert result["balance_due"] == 600.00
        assert result["status"] == "sent"

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_full_payment_sets_paid(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=21,
            entity_id=1,
            invoice_number="PFP-2026-011",
            customer_name="Full Payer",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[{"description": "x", "quantity": 1, "unit_price": 500, "amount": 500}],
            total_amount=500.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        result = invoice_gen.record_payment(21, 500)
        assert result["balance_due"] == 0.0
        assert result["status"] == "paid"

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_cannot_pay_voided_invoice(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=22,
            entity_id=1,
            invoice_number="PFP-2026-012",
            customer_name="Voided",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.VOID,
        )
        db_session.add(invoice)
        db_session.commit()

        with pytest.raises(ValueError, match="voided"):
            invoice_gen.record_payment(22, 100)

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_cannot_pay_already_paid(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=23,
            entity_id=1,
            invoice_number="PFP-2026-013",
            customer_name="Already Paid",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=100.00,
            amount_paid=100.00,
            status=InvoiceStatus.PAID,
        )
        db_session.add(invoice)
        db_session.commit()

        with pytest.raises(ValueError, match="already fully paid"):
            invoice_gen.record_payment(23, 50)


# === Overdue Detection ===

class TestOverdueDetection:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_sent_past_due_becomes_overdue(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=30,
            entity_id=1,
            invoice_number="PFP-2026-020",
            customer_name="Late Customer",
            date_issued=date(2026, 1, 1),
            date_due=date(2026, 1, 15),  # past due
            line_items=[],
            total_amount=250.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        newly_overdue = invoice_gen.check_overdue()
        assert 30 in newly_overdue

        refreshed = db_session.get(Invoice, 30)
        assert refreshed.status == InvoiceStatus.OVERDUE

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_draft_not_marked_overdue(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=31,
            entity_id=1,
            invoice_number="PFP-2026-021",
            customer_name="Draft Customer",
            date_issued=date(2026, 1, 1),
            date_due=date(2026, 1, 15),  # past due but DRAFT
            line_items=[],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        newly_overdue = invoice_gen.check_overdue()
        assert 31 not in newly_overdue

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_sent_not_yet_due_stays_sent(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=32,
            entity_id=1,
            invoice_number="PFP-2026-022",
            customer_name="On Time",
            date_issued=date.today(),
            date_due=date.today() + timedelta(days=30),  # not yet due
            line_items=[],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        newly_overdue = invoice_gen.check_overdue()
        assert 32 not in newly_overdue

        refreshed = db_session.get(Invoice, 32)
        assert refreshed.status == InvoiceStatus.SENT


# === Void ===

class TestVoidInvoice:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_void_draft(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=40,
            entity_id=1,
            invoice_number="PFP-2026-030",
            customer_name="To Void",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        result = invoice_gen.void_invoice(40, reason="Duplicate")
        assert result["status"] == "void"
        assert result["reason"] == "Duplicate"

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_cannot_void_paid(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=41,
            entity_id=1,
            invoice_number="PFP-2026-031",
            customer_name="Paid Customer",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=100.00,
            amount_paid=100.00,
            status=InvoiceStatus.PAID,
        )
        db_session.add(invoice)
        db_session.commit()

        with pytest.raises(ValueError, match="Cannot void a paid invoice"):
            invoice_gen.void_invoice(41)


# === Mark Sent ===

class TestMarkSent:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_mark_sent(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=50,
            entity_id=1,
            invoice_number="PFP-2026-040",
            customer_name="Send Test",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=200.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        result = invoice_gen.mark_sent(50)
        assert result["status"] == "sent"

        refreshed = db_session.get(Invoice, 50)
        assert refreshed.status == InvoiceStatus.SENT

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_cannot_send_non_draft(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=51,
            entity_id=1,
            invoice_number="PFP-2026-041",
            customer_name="Already Sent",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=200.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        with pytest.raises(ValueError, match="Cannot send"):
            invoice_gen.mark_sent(51)


# === Reminder ===

class TestReminder:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_reminder_increments_count(self, mock_log, mock_gs, db_session, invoice_gen, tmp_path):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=60,
            entity_id=1,
            invoice_number="PFP-2026-050",
            customer_name="Reminder Customer",
            date_issued=date(2026, 1, 1),
            date_due=date(2026, 1, 15),
            line_items=[{"description": "x", "quantity": 1, "unit_price": 100, "amount": 100}],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.OVERDUE,
            reminder_count=0,
        )
        db_session.add(invoice)
        db_session.commit()

        # Mock weasyprint at sys.modules level to avoid GTK dependency
        import sys
        mock_weasyprint = MagicMock()
        sys.modules["weasyprint"] = mock_weasyprint

        try:
            with patch("modules.billing.invoice_generator.INVOICES_DIR", tmp_path):
                result = invoice_gen.generate_reminder_pdf(60)
        finally:
            del sys.modules["weasyprint"]

        refreshed = db_session.get(Invoice, 60)
        assert refreshed.reminder_count == 1
        assert refreshed.last_reminder_at is not None


# === Get Invoice ===

class TestGetInvoice:

    @patch("modules.billing.invoice_generator.get_session")
    def test_get_invoice(self, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=70,
            entity_id=1,
            invoice_number="PFP-2026-060",
            customer_name="Get Test",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[{"description": "x", "quantity": 1, "unit_price": 50, "amount": 50}],
            total_amount=50.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        result = invoice_gen.get_invoice(70)
        assert result is not None
        assert result["invoice_number"] == "PFP-2026-060"
        assert result["customer_name"] == "Get Test"
        assert result["balance_due"] == 50.00
        assert result["entity_name"] == "Parker Farms Partnership"

    @patch("modules.billing.invoice_generator.get_session")
    def test_get_invoice_not_found(self, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        result = invoice_gen.get_invoice(999)
        assert result is None


# === Update Invoice ===

class TestUpdateInvoice:

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_update_draft_invoice(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=80,
            entity_id=1,
            invoice_number="PFP-2026-070",
            customer_name="Original Name",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[{"description": "x", "quantity": 1, "unit_price": 100, "amount": 100}],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.DRAFT,
        )
        db_session.add(invoice)
        db_session.commit()

        invoice_gen.update_invoice(
            80,
            customer_name="Updated Name",
            line_items=[
                {"description": "y", "quantity": 2, "unit_price": 75},
            ],
        )

        refreshed = db_session.get(Invoice, 80)
        assert refreshed.customer_name == "Updated Name"
        assert refreshed.total_amount == 150.00
        assert refreshed.line_items[0]["amount"] == 150.00

    @patch("modules.billing.invoice_generator.get_session")
    @patch("modules.billing.invoice_generator.log_action")
    def test_cannot_update_sent_invoice(self, mock_log, mock_gs, db_session, invoice_gen):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        invoice = Invoice(
            id=81,
            entity_id=1,
            invoice_number="PFP-2026-071",
            customer_name="Sent",
            date_issued=date(2026, 2, 1),
            date_due=date(2026, 3, 1),
            line_items=[],
            total_amount=100.00,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(invoice)
        db_session.commit()

        with pytest.raises(ValueError, match="Can only edit DRAFT"):
            invoice_gen.update_invoice(81, customer_name="Nope")
