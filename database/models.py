"""
Database models for AgentT.
Extends patterns from tax_assistant with document management,
approval workflows, and audit logging.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey,
    Boolean, Text, Enum, JSON
)
from sqlalchemy.orm import relationship, DeclarativeBase
from datetime import datetime, date
import enum


class Base(DeclarativeBase):
    pass


# === Enums ===

class EntityType(enum.Enum):
    ROW_CROP_FARM = "row_crop_farm"
    REAL_ESTATE = "real_estate"
    OTHER = "other"


class AccountingMethod(enum.Enum):
    CASH = "cash"
    ACCRUAL = "accrual"


class DocumentType(enum.Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    BANK_STATEMENT = "bank_statement"
    LEASE = "lease"
    CONTRACT = "contract"
    FSA_FORM = "fsa_form"
    TAX_DOCUMENT = "tax_document"
    INSURANCE = "insurance"
    UTILITY_BILL = "utility_bill"
    CORRESPONDENCE = "correspondence"
    UNKNOWN = "unknown"


class DocumentStatus(enum.Enum):
    PENDING = "pending"
    OCR_COMPLETE = "ocr_complete"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    FILED = "filed"
    ERROR = "error"


class TransactionType(enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class QBSyncStatus(enum.Enum):
    PENDING = "pending"
    IIF_GENERATED = "iif_generated"
    SYNCED = "synced"
    ERROR = "error"


class IIFType(enum.Enum):
    BILL = "bill"
    CHECK = "check"
    DEPOSIT = "deposit"


class InvoiceStatus(enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    VOID = "void"


class ApprovalStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalType(enum.Enum):
    QB_ENTRY = "qb_entry"
    INVOICE_SEND = "invoice_send"
    FSA_FILING = "fsa_filing"
    PAYMENT = "payment"


class AuditSeverity(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# === Models ===

class Entity(Base):
    """Business entity — one of the farm/real estate operations."""
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    slug = Column(String(50), nullable=False, unique=True)
    entity_type = Column(Enum(EntityType), nullable=False)
    state = Column(String(2), default="LA")
    accounting_method = Column(Enum(AccountingMethod), nullable=False, default=AccountingMethod.CASH)
    qb_company_file = Column(String(500))
    qb_class_name = Column(String(100))
    address = Column(String(500))
    phone = Column(String(20))
    email = Column(String(200))
    tax_id = Column(String(20))
    logo_path = Column(String(500))
    invoice_prefix = Column(String(10))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    documents = relationship("Document", back_populates="entity", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="entity", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="entity", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Entity(name='{self.name}', type='{self.entity_type.value}')>"


class Document(Base):
    """Any document processed by the system (scanned, OCR'd, classified, filed)."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"))
    original_filename = Column(String(500), nullable=False)
    stored_path = Column(String(500))
    document_type = Column(Enum(DocumentType), default=DocumentType.UNKNOWN)
    ocr_text = Column(Text)
    extracted_data = Column(JSON)
    ocr_confidence = Column(Float)
    classification_confidence = Column(Float)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING)
    scanned_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    filed_at = Column(DateTime)
    error_message = Column(Text)

    # Relationships
    entity = relationship("Entity", back_populates="documents")
    transactions = relationship("Transaction", back_populates="document")

    def __repr__(self):
        return f"<Document(file='{self.original_filename}', type='{self.document_type.value}', status='{self.status.value}')>"


class Transaction(Base):
    """Financial transaction (expense or income) extracted from documents or entered manually."""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"))
    transaction_type = Column(Enum(TransactionType), nullable=False)
    date = Column(Date, nullable=False)
    vendor_customer = Column(String(200))
    description = Column(Text)
    amount = Column(Float, nullable=False)
    category = Column(String(100))
    qb_account = Column(String(200))
    iif_type = Column(Enum(IIFType))
    qb_sync_status = Column(Enum(QBSyncStatus), default=QBSyncStatus.PENDING)
    iif_file_path = Column(String(500))
    approval_id = Column(Integer, ForeignKey("approvals.id"))
    reference_number = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    entity = relationship("Entity", back_populates="transactions")
    document = relationship("Document", back_populates="transactions")
    approval = relationship("ApprovalRequest", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction(date={self.date}, type='{self.transaction_type.value}', amount=${self.amount:,.2f})>"


class Invoice(Base):
    """Outbound invoice generated by the system."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    invoice_number = Column(String(50), nullable=False, unique=True)
    customer_name = Column(String(200), nullable=False)
    customer_address = Column(Text)
    date_issued = Column(Date, nullable=False, default=date.today)
    date_due = Column(Date, nullable=False)
    line_items = Column(JSON)
    total_amount = Column(Float, nullable=False)
    amount_paid = Column(Float, default=0.0)
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    pdf_path = Column(String(500))
    reminder_count = Column(Integer, default=0)
    last_reminder_at = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    entity = relationship("Entity", back_populates="invoices")

    @property
    def balance_due(self):
        return self.total_amount - (self.amount_paid or 0.0)

    def __repr__(self):
        return f"<Invoice(#{self.invoice_number}, customer='{self.customer_name}', total=${self.total_amount:,.2f})>"


class ApprovalRequest(Base):
    """Pending approval for sensitive operations (QB entries, invoice sends, etc.)."""
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("entities.id"))
    request_type = Column(Enum(ApprovalType), nullable=False)
    action_description = Column(Text, nullable=False)
    data_payload = Column(JSON)
    status = Column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    requested_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime)
    decided_by = Column(String(100))
    notes = Column(Text)

    # Relationships
    transactions = relationship("Transaction", back_populates="approval")

    def __repr__(self):
        return f"<ApprovalRequest(type='{self.request_type.value}', status='{self.status.value}')>"


class VendorMapping(Base):
    """Maps vendor names to Schedule F categories for automatic categorization."""
    __tablename__ = "vendor_mappings"

    id = Column(Integer, primary_key=True)
    vendor_name = Column(String(200), nullable=False, unique=True)  # lowercase
    vendor_display_name = Column(String(200))
    category_slug = Column(String(100), nullable=False)
    qb_account = Column(String(200))  # optional override
    entity_id = Column(Integer, ForeignKey("entities.id"))  # optional, for entity-specific overrides
    source = Column(String(50), default="manual")  # manual, claude_api, csv_import, seed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VendorMapping(vendor='{self.vendor_name}', category='{self.category_slug}')>"


class AuditLog(Base):
    """Immutable audit trail for all agent actions."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    entity_id = Column(Integer, ForeignKey("entities.id"))
    module = Column(String(50), nullable=False)
    action = Column(String(100), nullable=False)
    detail = Column(JSON)
    user = Column(String(100), default="system")
    severity = Column(Enum(AuditSeverity), default=AuditSeverity.INFO)

    def __repr__(self):
        return f"<AuditLog(time={self.timestamp}, module='{self.module}', action='{self.action}')>"
