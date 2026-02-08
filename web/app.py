"""
FastAPI web dashboard for AgentT.
Provides document monitoring, approval workflows, and system status.
"""

import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from database.db import get_db_session
from database.models import (
    Document, Entity, ApprovalRequest, AuditLog, Transaction, VendorMapping,
    DocumentStatus, ApprovalStatus, TransactionType, IIFType, QBSyncStatus,
    ApprovalType,
)
from config.entities import FARM_EXPENSE_CATEGORIES, FARM_INCOME_CATEGORIES
from config.qb_accounts import get_qb_account

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent

app = FastAPI(title="AgentT", description="Farm Office Automation Agent")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


# === Dashboard ===

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db_session)):
    """Main dashboard — overview of recent activity."""
    recent_docs = (
        db.query(Document)
        .order_by(Document.scanned_at.desc())
        .limit(20)
        .all()
    )

    pending_approvals = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status == ApprovalStatus.PENDING)
        .order_by(ApprovalRequest.requested_at.desc())
        .all()
    )

    entities = db.query(Entity).filter(Entity.active == True).all()

    total_txns = db.query(Transaction).count()
    pending_txns = db.query(Transaction).filter(
        Transaction.qb_sync_status == QBSyncStatus.PENDING
    ).count()
    iif_ready_txns = db.query(Transaction).filter(
        Transaction.qb_sync_status == QBSyncStatus.IIF_GENERATED
    ).count()
    synced_txns = db.query(Transaction).filter(
        Transaction.qb_sync_status == QBSyncStatus.SYNCED
    ).count()

    stats = {
        "total_documents": db.query(Document).count(),
        "pending_documents": db.query(Document).filter(
            Document.status.in_([DocumentStatus.PENDING, DocumentStatus.OCR_COMPLETE, DocumentStatus.CLASSIFIED])
        ).count(),
        "filed_documents": db.query(Document).filter(Document.status == DocumentStatus.FILED).count(),
        "error_documents": db.query(Document).filter(Document.status == DocumentStatus.ERROR).count(),
        "pending_approvals": len(pending_approvals),
        "total_transactions": total_txns,
        "pending_transactions": pending_txns,
        "iif_ready": iif_ready_txns,
        "synced_transactions": synced_txns,
    }

    recent_audit = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_docs": recent_docs,
        "pending_approvals": pending_approvals,
        "entities": entities,
        "stats": stats,
        "recent_audit": recent_audit,
    })


# === Documents ===

@app.get("/documents", response_class=HTMLResponse)
async def documents_list(request: Request, db: Session = Depends(get_db_session)):
    """All documents view."""
    docs = (
        db.query(Document)
        .order_by(Document.scanned_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse("documents.html", {
        "request": request,
        "documents": docs,
    })


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_detail(doc_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Single document detail view."""
    doc = db.get(Document, doc_id)
    if not doc:
        return HTMLResponse("Document not found", status_code=404)
    return templates.TemplateResponse("document_detail.html", {
        "request": request,
        "doc": doc,
    })


# === Create Transaction from Document ===

@app.get("/documents/{doc_id}/create-transaction", response_class=HTMLResponse)
async def create_transaction_form(doc_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Show transaction creation form pre-filled from document extracted data."""
    doc = db.get(Document, doc_id)
    if not doc:
        return HTMLResponse("Document not found", status_code=404)

    entities = db.query(Entity).filter(Entity.active == True).all()

    # Pre-fill from extracted data
    extracted = doc.extracted_data or {}
    prefill = {
        "vendor": extracted.get("vendor_name", extracted.get("vendor", "")),
        "date": extracted.get("date", extracted.get("invoice_date", "")),
        "amount": extracted.get("total_amount", extracted.get("amount", extracted.get("total", ""))),
        "reference_number": extracted.get("invoice_number", extracted.get("reference_number", "")),
        "description": extracted.get("description", extracted.get("summary", "")),
    }

    # Determine default IIF type and transaction type from document type
    doc_type = doc.document_type.value if doc.document_type else "unknown"
    if doc_type in ("invoice", "utility_bill"):
        default_iif_type = "bill"
        default_txn_type = "expense"
    elif doc_type == "receipt":
        default_iif_type = "check"
        default_txn_type = "expense"
    elif doc_type == "bank_statement":
        default_iif_type = "deposit"
        default_txn_type = "income"
    else:
        default_iif_type = "bill"
        default_txn_type = "expense"

    # Try to suggest a category
    suggestion = None
    categorizer = getattr(request.app.state, "categorizer", None)
    if categorizer and prefill.get("vendor"):
        try:
            suggestion = categorizer.categorize(
                vendor_name=prefill["vendor"],
                description=prefill.get("description", ""),
                amount=float(prefill["amount"]) if prefill.get("amount") else 0.0,
                document_text=doc.ocr_text or "",
                transaction_type=default_txn_type,
            )
        except Exception as e:
            logger.warning(f"Categorization failed: {e}")

    selected_entity_id = doc.entity_id or (entities[0].id if entities else None)

    return templates.TemplateResponse("create_transaction.html", {
        "request": request,
        "doc": doc,
        "entities": entities,
        "prefill": prefill,
        "default_iif_type": default_iif_type,
        "default_txn_type": default_txn_type,
        "suggestion": suggestion,
        "selected_entity_id": selected_entity_id,
        "expense_categories": FARM_EXPENSE_CATEGORIES,
        "income_categories": FARM_INCOME_CATEGORIES,
    })


@app.post("/documents/{doc_id}/create-transaction")
async def create_transaction_submit(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db_session),
    entity_id: int = Form(...),
    transaction_type: str = Form(...),
    iif_type: str = Form(...),
    date: str = Form(...),
    vendor_customer: str = Form(""),
    amount: float = Form(...),
    category: str = Form(""),
    qb_account: str = Form(""),
    reference_number: str = Form(""),
    description: str = Form(""),
    save_vendor_mapping: str = Form(None),
):
    """Create a transaction from document data and submit for approval."""
    doc = db.get(Document, doc_id)
    if not doc:
        return HTMLResponse("Document not found", status_code=404)

    # Parse date
    try:
        txn_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        txn_date = datetime.today().date()

    # If no QB account provided, look it up from category
    if not qb_account and category:
        qb_account = get_qb_account(category, transaction_type) or ""

    # Create transaction
    txn = Transaction(
        entity_id=entity_id,
        document_id=doc_id,
        transaction_type=TransactionType(transaction_type),
        iif_type=IIFType(iif_type),
        date=txn_date,
        vendor_customer=vendor_customer,
        amount=amount,
        category=category,
        qb_account=qb_account,
        reference_number=reference_number,
        description=description,
        qb_sync_status=QBSyncStatus.PENDING,
    )
    db.add(txn)
    db.flush()

    # Save vendor mapping if requested
    if save_vendor_mapping and vendor_customer and category:
        categorizer = getattr(request.app.state, "categorizer", None)
        if categorizer:
            categorizer.learn_vendor(vendor_customer, category)

    # Create approval request
    approval_engine = getattr(request.app.state, "approval_engine", None)
    if approval_engine:
        approval_engine.create_approval(
            entity_id=entity_id,
            request_type=ApprovalType.QB_ENTRY,
            action_description=f"{iif_type.upper()} ${amount:.2f} — {vendor_customer or 'Unknown'} — {category}",
            data_payload={
                "transaction_id": txn.id,
                "document_id": doc_id,
                "vendor": vendor_customer,
                "amount": amount,
                "date": str(txn_date),
                "category": category,
                "qb_account": qb_account,
                "iif_type": iif_type,
            },
            transaction_id=txn.id,
        )

    return RedirectResponse(url="/approvals", status_code=303)


# === Approvals ===

@app.get("/approvals", response_class=HTMLResponse)
async def approvals_list(request: Request, db: Session = Depends(get_db_session)):
    """Approval queue view."""
    pending = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status == ApprovalStatus.PENDING)
        .order_by(ApprovalRequest.requested_at.desc())
        .all()
    )

    decided = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status != ApprovalStatus.PENDING)
        .order_by(ApprovalRequest.decided_at.desc())
        .limit(20)
        .all()
    )

    # Attach entity names
    entity_cache = {}
    for approval in pending + decided:
        if approval.entity_id:
            if approval.entity_id not in entity_cache:
                entity = db.get(Entity, approval.entity_id)
                entity_cache[approval.entity_id] = entity.name if entity else "—"
            approval.entity_name = entity_cache[approval.entity_id]
        else:
            approval.entity_name = "—"

    return templates.TemplateResponse("approvals.html", {
        "request": request,
        "pending": pending,
        "decided": decided,
    })


@app.get("/approvals/{approval_id}", response_class=HTMLResponse)
async def approval_detail_view(approval_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Single approval detail view."""
    approval = db.get(ApprovalRequest, approval_id)
    if not approval:
        return HTMLResponse("Approval not found", status_code=404)

    entity_name = "—"
    if approval.entity_id:
        entity = db.get(Entity, approval.entity_id)
        if entity:
            entity_name = entity.name

    return templates.TemplateResponse("approval_detail.html", {
        "request": request,
        "approval": approval,
        "entity_name": entity_name,
    })


@app.post("/approvals/{approval_id}/decide")
async def approval_decide(
    approval_id: int,
    request: Request,
    decision: str = Form(...),
    notes: str = Form(""),
):
    """Approve or reject an approval request."""
    approval_engine = getattr(request.app.state, "approval_engine", None)
    if not approval_engine:
        return HTMLResponse("Approval engine not configured", status_code=500)

    try:
        approval_engine.decide(approval_id, decision, decided_by="user", notes=notes)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    return RedirectResponse(url="/approvals", status_code=303)


# === Transactions ===

@app.get("/transactions", response_class=HTMLResponse)
async def transactions_list(request: Request, db: Session = Depends(get_db_session)):
    """All transactions view."""
    transactions = (
        db.query(Transaction)
        .order_by(Transaction.created_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse("transactions.html", {
        "request": request,
        "transactions": transactions,
    })


@app.get("/transactions/{txn_id}", response_class=HTMLResponse)
async def transaction_detail_view(txn_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Single transaction detail view with IIF preview."""
    txn = db.get(Transaction, txn_id)
    if not txn:
        return HTMLResponse("Transaction not found", status_code=404)

    # Generate IIF preview
    iif_preview = None
    iif_generator = getattr(request.app.state, "iif_generator", None)
    if iif_generator:
        try:
            iif_preview = iif_generator.preview_iif(txn_id)
        except Exception as e:
            logger.warning(f"IIF preview failed: {e}")

    return templates.TemplateResponse("transaction_detail.html", {
        "request": request,
        "txn": txn,
        "iif_preview": iif_preview,
    })


@app.get("/transactions/{txn_id}/download-iif")
async def download_iif(txn_id: int, db: Session = Depends(get_db_session)):
    """Download the generated IIF file for a transaction."""
    txn = db.get(Transaction, txn_id)
    if not txn:
        return HTMLResponse("Transaction not found", status_code=404)
    if not txn.iif_file_path:
        return HTMLResponse("No IIF file generated yet", status_code=404)

    file_path = Path(txn.iif_file_path)
    if not file_path.exists():
        return HTMLResponse("IIF file not found on disk", status_code=404)

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@app.post("/transactions/{txn_id}/mark-synced")
async def mark_synced(txn_id: int, db: Session = Depends(get_db_session)):
    """Mark a transaction as synced to QuickBooks."""
    txn = db.get(Transaction, txn_id)
    if not txn:
        return HTMLResponse("Transaction not found", status_code=404)

    txn.qb_sync_status = QBSyncStatus.SYNCED

    from core.audit import log_action
    log_action(
        "quickbooks",
        "transaction_synced",
        detail={"transaction_id": txn_id},
        entity_id=txn.entity_id,
        user="user",
    )

    return RedirectResponse(url="/transactions", status_code=303)


# === Vendors ===

@app.get("/vendors", response_class=HTMLResponse)
async def vendors_list(request: Request, db: Session = Depends(get_db_session)):
    """Vendor mapping management."""
    mappings = (
        db.query(VendorMapping)
        .order_by(VendorMapping.vendor_display_name)
        .all()
    )
    return templates.TemplateResponse("vendors.html", {
        "request": request,
        "mappings": mappings,
        "expense_categories": FARM_EXPENSE_CATEGORIES,
        "income_categories": FARM_INCOME_CATEGORIES,
        "message": None,
        "error": None,
    })


@app.post("/vendors/add")
async def vendor_add(
    request: Request,
    db: Session = Depends(get_db_session),
    vendor_name: str = Form(...),
    category_slug: str = Form(...),
):
    """Add or update a vendor mapping."""
    from config.qb_accounts import save_vendor_mapping

    try:
        save_vendor_mapping(vendor_name, category_slug, source="manual")
        message = f"Saved mapping: {vendor_name} -> {category_slug}"
        error = None
    except Exception as e:
        message = None
        error = f"Failed to save mapping: {e}"

    mappings = (
        db.query(VendorMapping)
        .order_by(VendorMapping.vendor_display_name)
        .all()
    )
    return templates.TemplateResponse("vendors.html", {
        "request": request,
        "mappings": mappings,
        "expense_categories": FARM_EXPENSE_CATEGORIES,
        "income_categories": FARM_INCOME_CATEGORIES,
        "message": message,
        "error": error,
    })


# === Audit ===

@app.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request, db: Session = Depends(get_db_session)):
    """Audit log view."""
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse("audit.html", {
        "request": request,
        "entries": entries,
    })


# === API Endpoints (for HTMX partial updates) ===

@app.get("/api/stats")
async def api_stats(db: Session = Depends(get_db_session)):
    """Return current stats as JSON."""
    return {
        "total_documents": db.query(Document).count(),
        "filed_documents": db.query(Document).filter(Document.status == DocumentStatus.FILED).count(),
        "pending_approvals": db.query(ApprovalRequest).filter(ApprovalRequest.status == ApprovalStatus.PENDING).count(),
        "total_transactions": db.query(Transaction).count(),
        "synced_transactions": db.query(Transaction).filter(Transaction.qb_sync_status == QBSyncStatus.SYNCED).count(),
    }


@app.post("/api/categorize", response_class=HTMLResponse)
async def api_categorize(
    request: Request,
    vendor_customer: str = Form(""),
    transaction_type: str = Form("expense"),
):
    """HTMX endpoint: suggest category for a vendor name."""
    categorizer = getattr(request.app.state, "categorizer", None)
    if not categorizer or not vendor_customer.strip():
        return HTMLResponse("")

    try:
        suggestion = categorizer.categorize(
            vendor_name=vendor_customer,
            transaction_type=transaction_type,
        )
        source_label = suggestion.get("source", "unknown")
        confidence = suggestion.get("confidence", 0)
        category = suggestion.get("category", "")
        qb_account = suggestion.get("qb_account", "")

        return HTMLResponse(f"""
        <div class="flash flash-success" style="margin-top: 0;">
            Suggested: <strong>{category.replace('_', ' ').title()}</strong>
            ({qb_account}) &mdash; {source_label}
            ({confidence:.0%} confidence)
        </div>
        """)
    except Exception as e:
        logger.warning(f"HTMX categorize failed: {e}")
        return HTMLResponse("")
