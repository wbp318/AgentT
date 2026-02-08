"""
Invoice generation module for AgentT.
Creates, manages, and renders PDF invoices for farm entities.
"""

import logging
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from database.db import get_session
from database.models import Invoice, Entity, InvoiceStatus
from core.audit import log_action
from config.settings import INVOICES_DIR

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class InvoiceGenerator:
    """Generates and manages outbound invoices."""

    def setup(self, event_bus):
        self.event_bus = event_bus
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=True,
        )

    def start(self):
        pass

    def stop(self):
        pass

    def create_invoice(self, entity_id, customer_name, customer_address,
                       date_due, line_items, notes="") -> int:
        """
        Create a new DRAFT invoice.

        Args:
            entity_id: FK to entities table
            customer_name: Bill-to name
            customer_address: Bill-to address
            date_due: Payment due date
            line_items: List of dicts with description, quantity, unit_price
            notes: Optional notes

        Returns:
            invoice_id
        """
        with get_session() as session:
            entity = session.get(Entity, entity_id)
            if not entity:
                raise ValueError(f"Entity {entity_id} not found")

            # Calculate total from line items
            total_amount = 0.0
            for item in line_items:
                qty = float(item.get("quantity", 1))
                price = float(item.get("unit_price", 0))
                item["amount"] = round(qty * price, 2)
                total_amount += item["amount"]
            total_amount = round(total_amount, 2)

            # Generate invoice number: {prefix}-{YYYY}-{NNN}
            prefix = entity.invoice_prefix or entity.slug.upper()[:3]
            year = date.today().year
            invoice_number = self._next_invoice_number(session, entity_id, prefix, year)

            invoice = Invoice(
                entity_id=entity_id,
                invoice_number=invoice_number,
                customer_name=customer_name,
                customer_address=customer_address,
                date_issued=date.today(),
                date_due=date_due,
                line_items=line_items,
                total_amount=total_amount,
                amount_paid=0.0,
                status=InvoiceStatus.DRAFT,
                notes=notes,
            )
            session.add(invoice)
            session.flush()
            invoice_id = invoice.id

        log_action(
            "billing",
            "invoice_created",
            detail={"invoice_id": invoice_id, "invoice_number": invoice_number,
                    "customer": customer_name, "total": total_amount},
            entity_id=entity_id,
        )

        return invoice_id

    def generate_pdf(self, invoice_id) -> str:
        """
        Render invoice as PDF via WeasyPrint.

        Returns:
            Path to generated PDF file.
        """
        from weasyprint import HTML

        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")

            entity = session.get(Entity, invoice.entity_id)

            # Render HTML
            template = self.jinja_env.get_template("invoice.html")
            html_content = template.render(
                invoice=invoice,
                entity=entity,
                line_items=invoice.line_items or [],
            )

            # Build output path: data/invoices/{entity_slug}/{YYYY}/{invoice_number}.pdf
            out_dir = INVOICES_DIR / entity.slug / str(invoice.date_issued.year)
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = out_dir / f"{invoice.invoice_number}.pdf"

            # Generate PDF
            HTML(string=html_content).write_pdf(str(pdf_path))

            # Update record
            invoice.pdf_path = str(pdf_path)
            result_path = str(pdf_path)
            inv_number = invoice.invoice_number
            eid = invoice.entity_id

        log_action(
            "billing",
            "invoice_pdf_generated",
            detail={"invoice_id": invoice_id, "invoice_number": inv_number,
                    "pdf_path": result_path},
            entity_id=eid,
        )

        return result_path

    def generate_reminder_pdf(self, invoice_id) -> str:
        """
        Generate an overdue reminder letter PDF.

        Returns:
            Path to generated reminder PDF.
        """
        from weasyprint import HTML

        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")

            entity = session.get(Entity, invoice.entity_id)

            invoice.reminder_count = (invoice.reminder_count or 0) + 1
            invoice.last_reminder_at = datetime.utcnow()
            reminder_num = invoice.reminder_count

            days_overdue = (date.today() - invoice.date_due).days if invoice.date_due else 0

            template = self.jinja_env.get_template("reminder.html")
            html_content = template.render(
                invoice=invoice,
                entity=entity,
                days_overdue=days_overdue,
                reminder_number=reminder_num,
            )

            out_dir = INVOICES_DIR / entity.slug / str(invoice.date_issued.year)
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = out_dir / f"{invoice.invoice_number}_reminder_{reminder_num}.pdf"

            HTML(string=html_content).write_pdf(str(pdf_path))

            result_path = str(pdf_path)
            inv_number = invoice.invoice_number
            eid = invoice.entity_id

        log_action(
            "billing",
            "reminder_generated",
            detail={"invoice_id": invoice_id, "invoice_number": inv_number,
                    "reminder_count": reminder_num, "pdf_path": result_path},
            entity_id=eid,
        )

        return result_path

    def mark_sent(self, invoice_id) -> dict:
        """Mark invoice as SENT. Returns invoice info dict."""
        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")
            if invoice.status not in (InvoiceStatus.DRAFT,):
                raise ValueError(f"Cannot send invoice in status {invoice.status.value}")

            invoice.status = InvoiceStatus.SENT
            result = {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
            }
            eid = invoice.entity_id

        log_action(
            "billing",
            "invoice_sent",
            detail=result,
            entity_id=eid,
            user="user",
        )

        return result

    def record_payment(self, invoice_id, amount, payment_date=None, notes="") -> dict:
        """
        Record a payment against an invoice.
        If balance_due <= 0 after payment, status becomes PAID.

        Returns:
            Invoice info dict with updated amounts.
        """
        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")
            if invoice.status == InvoiceStatus.VOID:
                raise ValueError("Cannot record payment on a voided invoice")
            if invoice.status == InvoiceStatus.PAID:
                raise ValueError("Invoice is already fully paid")

            invoice.amount_paid = round((invoice.amount_paid or 0.0) + float(amount), 2)

            if invoice.balance_due <= 0:
                invoice.status = InvoiceStatus.PAID

            result = {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "payment_amount": float(amount),
                "total_paid": invoice.amount_paid,
                "balance_due": invoice.balance_due,
                "status": invoice.status.value,
            }
            eid = invoice.entity_id

        log_action(
            "billing",
            "payment_recorded",
            detail=result,
            entity_id=eid,
            user="user",
        )

        return result

    def void_invoice(self, invoice_id, reason="") -> dict:
        """Void an invoice. Cannot void PAID invoices."""
        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")
            if invoice.status == InvoiceStatus.PAID:
                raise ValueError("Cannot void a paid invoice")

            invoice.status = InvoiceStatus.VOID
            result = {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
                "reason": reason,
            }
            eid = invoice.entity_id

        log_action(
            "billing",
            "invoice_voided",
            detail=result,
            entity_id=eid,
            user="user",
        )

        return result

    def check_overdue(self) -> list[int]:
        """
        Find SENT invoices past their due date and flip to OVERDUE.

        Returns:
            List of invoice IDs that were newly marked overdue.
        """
        newly_overdue = []
        today = date.today()

        with get_session() as session:
            invoices = (
                session.query(Invoice)
                .filter(
                    Invoice.status == InvoiceStatus.SENT,
                    Invoice.date_due < today,
                )
                .all()
            )

            for inv in invoices:
                inv.status = InvoiceStatus.OVERDUE
                newly_overdue.append(inv.id)
                log_action(
                    "billing",
                    "invoice_overdue",
                    detail={"invoice_id": inv.id, "invoice_number": inv.invoice_number,
                            "date_due": str(inv.date_due), "days_overdue": (today - inv.date_due).days},
                    entity_id=inv.entity_id,
                )

        return newly_overdue

    def get_invoice(self, invoice_id) -> dict | None:
        """Load an invoice by ID. Returns dict or None."""
        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                return None
            entity = session.get(Entity, invoice.entity_id)
            return {
                "id": invoice.id,
                "entity_id": invoice.entity_id,
                "entity_name": entity.name if entity else "",
                "entity_slug": entity.slug if entity else "",
                "invoice_number": invoice.invoice_number,
                "customer_name": invoice.customer_name,
                "customer_address": invoice.customer_address,
                "date_issued": invoice.date_issued,
                "date_due": invoice.date_due,
                "line_items": invoice.line_items or [],
                "total_amount": invoice.total_amount,
                "amount_paid": invoice.amount_paid or 0.0,
                "balance_due": invoice.balance_due,
                "status": invoice.status.value,
                "pdf_path": invoice.pdf_path,
                "reminder_count": invoice.reminder_count or 0,
                "notes": invoice.notes,
                "created_at": invoice.created_at,
                "updated_at": invoice.updated_at,
            }

    def update_invoice(self, invoice_id, **kwargs) -> dict:
        """Update a DRAFT invoice's fields. Returns updated info dict."""
        with get_session() as session:
            invoice = session.get(Invoice, invoice_id)
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")
            if invoice.status != InvoiceStatus.DRAFT:
                raise ValueError("Can only edit DRAFT invoices")

            if "customer_name" in kwargs:
                invoice.customer_name = kwargs["customer_name"]
            if "customer_address" in kwargs:
                invoice.customer_address = kwargs["customer_address"]
            if "date_due" in kwargs:
                invoice.date_due = kwargs["date_due"]
            if "notes" in kwargs:
                invoice.notes = kwargs["notes"]
            if "line_items" in kwargs:
                line_items = kwargs["line_items"]
                total = 0.0
                for item in line_items:
                    qty = float(item.get("quantity", 1))
                    price = float(item.get("unit_price", 0))
                    item["amount"] = round(qty * price, 2)
                    total += item["amount"]
                invoice.line_items = line_items
                invoice.total_amount = round(total, 2)

            result = {
                "id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "status": invoice.status.value,
            }
            eid = invoice.entity_id

        log_action(
            "billing",
            "invoice_updated",
            detail=result,
            entity_id=eid,
            user="user",
        )

        return result

    def _next_invoice_number(self, session, entity_id, prefix, year) -> str:
        """Generate next sequential invoice number for an entity/year."""
        pattern = f"{prefix}-{year}-%"
        last = (
            session.query(Invoice)
            .filter(
                Invoice.entity_id == entity_id,
                Invoice.invoice_number.like(pattern),
            )
            .order_by(Invoice.invoice_number.desc())
            .first()
        )

        if last:
            # Extract the sequence number from the last invoice number
            last_seq = int(last.invoice_number.split("-")[-1])
            next_seq = last_seq + 1
        else:
            next_seq = 1

        return f"{prefix}-{year}-{next_seq:03d}"
