"""
IIF file generator for QuickBooks Desktop import.
Generates tab-separated IIF files for BILL, CHECK, and DEPOSIT transactions.

IIF format rules:
- Tab-separated values
- CRLF line endings
- ASCII-safe characters
- TRNS + SPL lines must balance to zero
- UTF-8 encoding without BOM
"""

import logging
from datetime import datetime, date
from pathlib import Path

from core.events import EventBus, Event, APPROVAL_DECIDED, IIF_GENERATED
from core.audit import log_action
from database.db import get_session
from database.models import (
    Transaction, Entity, IIFType, QBSyncStatus, ApprovalStatus
)
from config.settings import IIF_OUTPUT_DIR
from config.qb_accounts import DEFAULT_ACCOUNTS

logger = logging.getLogger(__name__)

TAB = "\t"
CRLF = "\r\n"


class IIFGenerator:
    """
    Generates IIF files for QuickBooks Desktop import.
    Subscribes to APPROVAL_DECIDED — auto-generates IIF when a QB_ENTRY approval is approved.
    """

    def setup(self, event_bus):
        self._event_bus = event_bus
        event_bus.subscribe(APPROVAL_DECIDED, self._handle_approval_decided)

    def _handle_approval_decided(self, event):
        """Handle approval decisions — generate IIF for approved QB entries."""
        if event.data.get("decision") != "approved":
            return

        transaction_id = event.data.get("transaction_id")
        if not transaction_id:
            return

        try:
            self.generate_iif(transaction_id)
        except Exception as e:
            logger.error(f"Failed to generate IIF for transaction #{transaction_id}: {e}")

    def generate_iif(self, transaction_id):
        """Generate an IIF file for a single transaction.

        Args:
            transaction_id: Transaction ID to generate IIF for

        Returns:
            File path of the generated IIF file
        """
        with get_session() as session:
            txn = session.get(Transaction, transaction_id)
            if not txn:
                raise ValueError(f"Transaction #{transaction_id} not found")

            entity = session.get(Entity, txn.entity_id)
            if not entity:
                raise ValueError(f"Entity not found for transaction #{transaction_id}")

            iif_type = txn.iif_type
            if not iif_type:
                # Default based on transaction type
                if txn.transaction_type.value == "income":
                    iif_type = IIFType.DEPOSIT
                else:
                    iif_type = IIFType.BILL

            # Generate IIF content
            if iif_type == IIFType.BILL:
                content = self._format_bill(txn, entity)
            elif iif_type == IIFType.CHECK:
                content = self._format_check(txn, entity)
            elif iif_type == IIFType.DEPOSIT:
                content = self._format_deposit(txn, entity)
            else:
                raise ValueError(f"Unknown IIF type: {iif_type}")

            # Build file path
            txn_date = txn.date or date.today()
            date_folder = txn_date.strftime("%Y-%m")
            entity_dir = IIF_OUTPUT_DIR / entity.slug / date_folder
            entity_dir.mkdir(parents=True, exist_ok=True)

            filename = (
                f"{entity.slug}_{iif_type.value}_{txn_date.strftime('%Y%m%d')}"
                f"_{txn.id}.iif"
            )
            file_path = entity_dir / filename

            # Write IIF file
            file_path.write_text(content, encoding="utf-8", newline="")

            # Update transaction
            txn.iif_file_path = str(file_path)
            txn.qb_sync_status = QBSyncStatus.IIF_GENERATED

        log_action(
            "quickbooks",
            "iif_generated",
            detail={
                "transaction_id": transaction_id,
                "file_path": str(file_path),
                "iif_type": iif_type.value,
            },
            entity_id=entity.id if entity else None,
        )

        if self._event_bus:
            self._event_bus.emit(Event(IIF_GENERATED, {
                "transaction_id": transaction_id,
                "file_path": str(file_path),
                "iif_type": iif_type.value,
            }))

        logger.info(f"Generated IIF: {file_path}")
        return str(file_path)

    def generate_batch_iif(self, transaction_ids):
        """Generate a single IIF file containing multiple transactions.

        All transactions must belong to the same entity.

        Args:
            transaction_ids: List of transaction IDs

        Returns:
            File path of the generated IIF file
        """
        if not transaction_ids:
            raise ValueError("No transaction IDs provided")

        with get_session() as session:
            transactions = []
            entity = None

            for tid in transaction_ids:
                txn = session.get(Transaction, tid)
                if not txn:
                    raise ValueError(f"Transaction #{tid} not found")

                txn_entity = session.get(Entity, txn.entity_id)
                if entity is None:
                    entity = txn_entity
                elif txn_entity.id != entity.id:
                    raise ValueError("All transactions must belong to the same entity")

                transactions.append((txn, txn_entity))

            # Build combined IIF content
            lines = [self._iif_header()]
            for txn, ent in transactions:
                iif_type = txn.iif_type
                if not iif_type:
                    if txn.transaction_type.value == "income":
                        iif_type = IIFType.DEPOSIT
                    else:
                        iif_type = IIFType.BILL

                if iif_type == IIFType.BILL:
                    lines.append(self._format_bill_body(txn, ent))
                elif iif_type == IIFType.CHECK:
                    lines.append(self._format_check_body(txn, ent))
                elif iif_type == IIFType.DEPOSIT:
                    lines.append(self._format_deposit_body(txn, ent))

            content = CRLF.join(lines) + CRLF

            # Write batch file
            batch_date = date.today().strftime("%Y%m%d")
            date_folder = date.today().strftime("%Y-%m")
            entity_dir = IIF_OUTPUT_DIR / entity.slug / date_folder
            entity_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{entity.slug}_batch_{batch_date}.iif"
            file_path = entity_dir / filename
            file_path.write_text(content, encoding="utf-8", newline="")

            # Update all transactions
            for txn, _ in transactions:
                txn.iif_file_path = str(file_path)
                txn.qb_sync_status = QBSyncStatus.IIF_GENERATED

        logger.info(f"Generated batch IIF with {len(transactions)} transactions: {file_path}")
        return str(file_path)

    def preview_iif(self, transaction_id):
        """Generate IIF content for preview without writing to disk.

        Args:
            transaction_id: Transaction ID

        Returns:
            IIF content string
        """
        with get_session() as session:
            txn = session.get(Transaction, transaction_id)
            if not txn:
                raise ValueError(f"Transaction #{transaction_id} not found")

            entity = session.get(Entity, txn.entity_id)

            iif_type = txn.iif_type
            if not iif_type:
                if txn.transaction_type.value == "income":
                    iif_type = IIFType.DEPOSIT
                else:
                    iif_type = IIFType.BILL

            if iif_type == IIFType.BILL:
                return self._format_bill(txn, entity)
            elif iif_type == IIFType.CHECK:
                return self._format_check(txn, entity)
            elif iif_type == IIFType.DEPOSIT:
                return self._format_deposit(txn, entity)

    def _iif_header(self):
        """Generate IIF header rows."""
        trns_header = TAB.join([
            "!TRNS", "TRNSID", "TRNSTYPE", "DATE", "ACCNT", "NAME",
            "AMOUNT", "DOCNUM", "MEMO"
        ])
        spl_header = TAB.join([
            "!SPL", "SPLID", "TRNSTYPE", "DATE", "ACCNT", "NAME",
            "AMOUNT", "DOCNUM", "MEMO"
        ])
        end_header = "!ENDTRNS"
        return CRLF.join([trns_header, spl_header, end_header])

    def _format_bill(self, txn, entity):
        """Format a complete BILL IIF file (header + body)."""
        return self._iif_header() + CRLF + self._format_bill_body(txn, entity) + CRLF

    def _format_bill_body(self, txn, entity):
        """Format BILL transaction body (TRNS + SPL + ENDTRNS).

        BILL: AP entry from vendor invoice.
        TRNS line: negative amount on Accounts Payable
        SPL line: positive amount on expense account
        """
        amount = abs(txn.amount)
        date_str = self._format_date(txn.date)
        vendor = self._safe_str(txn.vendor_customer or "")
        memo = self._safe_str(txn.description or "")
        ref_num = self._safe_str(txn.reference_number or "")
        expense_account = txn.qb_account or "Other Farm Expenses"
        ap_account = DEFAULT_ACCOUNTS["accounts_payable"]

        trns = TAB.join([
            "TRNS", "", "BILL", date_str, ap_account, vendor,
            f"-{amount:.2f}", ref_num, memo
        ])
        spl = TAB.join([
            "SPL", "", "BILL", date_str, expense_account, vendor,
            f"{amount:.2f}", ref_num, memo
        ])
        return CRLF.join([trns, spl, "ENDTRNS"])

    def _format_check(self, txn, entity):
        """Format a complete CHECK IIF file (header + body)."""
        return self._iif_header() + CRLF + self._format_check_body(txn, entity) + CRLF

    def _format_check_body(self, txn, entity):
        """Format CHECK transaction body (TRNS + SPL + ENDTRNS).

        CHECK: Direct payment from checking.
        TRNS line: negative amount on Checking
        SPL line: positive amount on expense account
        """
        amount = abs(txn.amount)
        date_str = self._format_date(txn.date)
        vendor = self._safe_str(txn.vendor_customer or "")
        memo = self._safe_str(txn.description or "")
        ref_num = self._safe_str(txn.reference_number or "")
        expense_account = txn.qb_account or "Other Farm Expenses"
        checking_account = DEFAULT_ACCOUNTS["checking"]

        trns = TAB.join([
            "TRNS", "", "CHECK", date_str, checking_account, vendor,
            f"-{amount:.2f}", ref_num, memo
        ])
        spl = TAB.join([
            "SPL", "", "CHECK", date_str, expense_account, vendor,
            f"{amount:.2f}", ref_num, memo
        ])
        return CRLF.join([trns, spl, "ENDTRNS"])

    def _format_deposit(self, txn, entity):
        """Format a complete DEPOSIT IIF file (header + body)."""
        return self._iif_header() + CRLF + self._format_deposit_body(txn, entity) + CRLF

    def _format_deposit_body(self, txn, entity):
        """Format DEPOSIT transaction body (TRNS + SPL + ENDTRNS).

        DEPOSIT: Income deposit to checking.
        TRNS line: positive amount on Checking
        SPL line: negative amount on income account
        """
        amount = abs(txn.amount)
        date_str = self._format_date(txn.date)
        customer = self._safe_str(txn.vendor_customer or "")
        memo = self._safe_str(txn.description or "")
        ref_num = self._safe_str(txn.reference_number or "")
        income_account = txn.qb_account or "Other Farm Income"
        checking_account = DEFAULT_ACCOUNTS["checking"]

        trns = TAB.join([
            "TRNS", "", "DEPOSIT", date_str, checking_account, "",
            f"{amount:.2f}", ref_num, memo
        ])
        spl = TAB.join([
            "SPL", "", "DEPOSIT", date_str, income_account, customer,
            f"-{amount:.2f}", ref_num, memo
        ])
        return CRLF.join([trns, spl, "ENDTRNS"])

    def _format_date(self, d):
        """Format a date as MM/DD/YYYY for IIF."""
        if isinstance(d, datetime):
            d = d.date()
        if isinstance(d, date):
            return d.strftime("%m/%d/%Y")
        return ""

    def _safe_str(self, s):
        """Make a string safe for IIF (no tabs, no CRLF, ASCII-safe)."""
        return str(s).replace("\t", " ").replace("\r", "").replace("\n", " ").strip()
