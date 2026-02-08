"""
FastAPI web dashboard for AgentT.
Provides document monitoring, approval workflows, and system status.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Depends, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session

from database.db import get_db_session
from database.models import (
    Document, Entity, ApprovalRequest, AuditLog, Transaction, VendorMapping, Invoice,
    DocumentStatus, ApprovalStatus, TransactionType, IIFType, QBSyncStatus,
    ApprovalType, InvoiceStatus,
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

    # Invoice stats
    total_invoices = db.query(Invoice).count()
    draft_invoices = db.query(Invoice).filter(Invoice.status == InvoiceStatus.DRAFT).count()
    overdue_invoices_count = db.query(Invoice).filter(Invoice.status == InvoiceStatus.OVERDUE).count()

    from sqlalchemy import func
    outstanding_result = (
        db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.amount_paid), 0))
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]))
        .scalar()
    )
    outstanding_amount = float(outstanding_result or 0)

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
        "total_invoices": total_invoices,
        "draft_invoices": draft_invoices,
        "outstanding_amount": outstanding_amount,
        "overdue_invoices": overdue_invoices_count,
    }

    recent_audit = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(10)
        .all()
    )

    # Overdue invoices for dashboard alert
    overdue_invs = (
        db.query(Invoice)
        .filter(Invoice.status == InvoiceStatus.OVERDUE)
        .order_by(Invoice.date_due)
        .all()
    )
    for inv in overdue_invs:
        inv.days_overdue = (date.today() - inv.date_due).days if inv.date_due else 0

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_docs": recent_docs,
        "pending_approvals": pending_approvals,
        "entities": entities,
        "stats": stats,
        "recent_audit": recent_audit,
        "overdue_invoices": overdue_invs,
    })


# === Documents ===

@app.get("/documents", response_class=HTMLResponse)
async def documents_list(
    request: Request,
    db: Session = Depends(get_db_session),
    entity: str = None,
    status: str = None,
):
    """All documents view with optional entity/status filters."""
    query = db.query(Document)

    if entity:
        ent = db.query(Entity).filter(Entity.slug == entity).first()
        if ent:
            query = query.filter(Document.entity_id == ent.id)
    if status:
        try:
            query = query.filter(Document.status == DocumentStatus(status))
        except ValueError:
            pass

    docs = query.order_by(Document.scanned_at.desc()).limit(100).all()
    entities = db.query(Entity).filter(Entity.active == True).all()
    statuses = [s.value for s in DocumentStatus]

    return templates.TemplateResponse("documents.html", {
        "request": request,
        "documents": docs,
        "entities": entities,
        "current_entity": entity or "",
        "current_status": status or "",
        "statuses": statuses,
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

    msg = quote(f"Transaction created for ${amount:.2f} — pending approval")
    return RedirectResponse(url=f"/approvals?msg={msg}&msg_type=success", status_code=303)


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

    msg = quote(f"Approval #{approval_id} {decision}")
    return RedirectResponse(url=f"/approvals?msg={msg}&msg_type=success", status_code=303)


# === Transactions ===

@app.get("/transactions", response_class=HTMLResponse)
async def transactions_list(
    request: Request,
    db: Session = Depends(get_db_session),
    entity: str = None,
    status: str = None,
):
    """All transactions view with optional entity/status filters."""
    query = db.query(Transaction)

    # Apply filters
    if entity:
        ent = db.query(Entity).filter(Entity.slug == entity).first()
        if ent:
            query = query.filter(Transaction.entity_id == ent.id)
    if status:
        try:
            query = query.filter(Transaction.qb_sync_status == QBSyncStatus(status))
        except ValueError:
            pass

    transactions = query.order_by(Transaction.created_at.desc()).limit(100).all()

    # Attach entity names
    entity_cache = {}
    for txn in transactions:
        if txn.entity_id not in entity_cache:
            ent_obj = db.get(Entity, txn.entity_id)
            entity_cache[txn.entity_id] = ent_obj.name if ent_obj else "—"
        txn.entity_name = entity_cache[txn.entity_id]

    entities = db.query(Entity).filter(Entity.active == True).all()
    statuses = [s.value for s in QBSyncStatus]

    return templates.TemplateResponse("transactions.html", {
        "request": request,
        "transactions": transactions,
        "entities": entities,
        "current_entity": entity or "",
        "current_status": status or "",
        "statuses": statuses,
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

    msg = quote(f"Transaction #{txn_id} marked as synced")
    return RedirectResponse(url=f"/transactions?msg={msg}&msg_type=success", status_code=303)


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
        msg = quote(f"Saved mapping: {vendor_name} -> {category_slug}")
        return RedirectResponse(url=f"/vendors?msg={msg}&msg_type=success", status_code=303)
    except Exception as e:
        msg = quote(f"Failed to save mapping: {e}")
        return RedirectResponse(url=f"/vendors?msg={msg}&msg_type=error", status_code=303)


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


# === Invoices ===

@app.get("/invoices", response_class=HTMLResponse)
async def invoices_list(
    request: Request,
    db: Session = Depends(get_db_session),
    entity: str = None,
    status: str = None,
):
    """Invoice list view with optional entity/status filters."""
    query = db.query(Invoice)

    if entity:
        ent = db.query(Entity).filter(Entity.slug == entity).first()
        if ent:
            query = query.filter(Invoice.entity_id == ent.id)
    if status:
        try:
            query = query.filter(Invoice.status == InvoiceStatus(status))
        except ValueError:
            pass

    invoices = query.order_by(Invoice.created_at.desc()).limit(100).all()

    # Attach entity names
    entity_cache = {}
    for inv in invoices:
        if inv.entity_id not in entity_cache:
            ent_obj = db.get(Entity, inv.entity_id)
            entity_cache[inv.entity_id] = ent_obj.name if ent_obj else "—"
        inv.entity_name = entity_cache[inv.entity_id]

    entities = db.query(Entity).filter(Entity.active == True).all()
    statuses = [s.value for s in InvoiceStatus]

    return templates.TemplateResponse("invoices.html", {
        "request": request,
        "invoices": invoices,
        "entities": entities,
        "current_entity": entity or "",
        "current_status": status or "",
        "statuses": statuses,
    })


@app.get("/invoices/create", response_class=HTMLResponse)
async def create_invoice_form(request: Request, db: Session = Depends(get_db_session)):
    """Invoice creation form."""
    entities = db.query(Entity).filter(Entity.active == True).all()
    # Only farm entities for now
    farm_entities = [e for e in entities if e.entity_type.value == "row_crop_farm"]
    return templates.TemplateResponse("create_invoice.html", {
        "request": request,
        "entities": farm_entities,
    })


@app.post("/invoices/create")
async def create_invoice_submit(
    request: Request,
    db: Session = Depends(get_db_session),
    entity_id: int = Form(...),
    customer_name: str = Form(...),
    customer_address: str = Form(""),
    date_due: str = Form(...),
    notes: str = Form(""),
):
    """Save a new invoice."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    # Parse line items from form data
    form_data = await request.form()
    line_items = []
    i = 0
    while True:
        desc_key = f"item_description_{i}"
        qty_key = f"item_quantity_{i}"
        price_key = f"item_unit_price_{i}"
        if desc_key not in form_data:
            break
        desc = form_data[desc_key]
        qty = form_data.get(qty_key, "1")
        price = form_data.get(price_key, "0")
        if desc.strip():
            line_items.append({
                "description": desc.strip(),
                "quantity": float(qty) if qty else 1,
                "unit_price": float(price) if price else 0,
            })
        i += 1

    if not line_items:
        return HTMLResponse("At least one line item is required", status_code=400)

    try:
        due = datetime.strptime(date_due, "%Y-%m-%d").date()
    except ValueError:
        return HTMLResponse("Invalid due date format", status_code=400)

    invoice_id = invoice_generator.create_invoice(
        entity_id=entity_id,
        customer_name=customer_name,
        customer_address=customer_address,
        date_due=due,
        line_items=line_items,
        notes=notes,
    )

    msg = quote("Invoice created successfully")
    return RedirectResponse(url=f"/invoices/{invoice_id}?msg={msg}&msg_type=success", status_code=303)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(invoice_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Invoice detail view."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    inv_data = invoice_generator.get_invoice(invoice_id)
    if not inv_data:
        return HTMLResponse("Invoice not found", status_code=404)

    return templates.TemplateResponse("invoice_detail.html", {
        "request": request,
        "inv": inv_data,
    })


@app.get("/invoices/{invoice_id}/edit", response_class=HTMLResponse)
async def edit_invoice_form(invoice_id: int, request: Request, db: Session = Depends(get_db_session)):
    """Edit form for DRAFT invoices."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    inv_data = invoice_generator.get_invoice(invoice_id)
    if not inv_data:
        return HTMLResponse("Invoice not found", status_code=404)
    if inv_data["status"] != "draft":
        return HTMLResponse("Can only edit DRAFT invoices", status_code=400)

    entities = db.query(Entity).filter(Entity.active == True).all()
    farm_entities = [e for e in entities if e.entity_type.value == "row_crop_farm"]

    return templates.TemplateResponse("edit_invoice.html", {
        "request": request,
        "inv": inv_data,
        "entities": farm_entities,
    })


@app.post("/invoices/{invoice_id}/edit")
async def edit_invoice_submit(
    invoice_id: int,
    request: Request,
    customer_name: str = Form(...),
    customer_address: str = Form(""),
    date_due: str = Form(...),
    notes: str = Form(""),
):
    """Save edits to a DRAFT invoice."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    # Parse line items from form data
    form_data = await request.form()
    line_items = []
    i = 0
    while True:
        desc_key = f"item_description_{i}"
        qty_key = f"item_quantity_{i}"
        price_key = f"item_unit_price_{i}"
        if desc_key not in form_data:
            break
        desc = form_data[desc_key]
        qty = form_data.get(qty_key, "1")
        price = form_data.get(price_key, "0")
        if desc.strip():
            line_items.append({
                "description": desc.strip(),
                "quantity": float(qty) if qty else 1,
                "unit_price": float(price) if price else 0,
            })
        i += 1

    try:
        due = datetime.strptime(date_due, "%Y-%m-%d").date()
    except ValueError:
        return HTMLResponse("Invalid due date format", status_code=400)

    try:
        invoice_generator.update_invoice(
            invoice_id,
            customer_name=customer_name,
            customer_address=customer_address,
            date_due=due,
            line_items=line_items,
            notes=notes,
        )
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    msg = quote("Invoice updated successfully")
    return RedirectResponse(url=f"/invoices/{invoice_id}?msg={msg}&msg_type=success", status_code=303)


@app.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: int, request: Request):
    """Download/regenerate invoice PDF."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    try:
        pdf_path = invoice_generator.generate_pdf(invoice_id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=404)

    return FileResponse(
        path=pdf_path,
        filename=Path(pdf_path).name,
        media_type="application/pdf",
    )


@app.post("/invoices/{invoice_id}/send")
async def invoice_send(invoice_id: int, request: Request):
    """Mark invoice as SENT."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    try:
        invoice_generator.mark_sent(invoice_id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    msg = quote("Invoice marked as sent")
    return RedirectResponse(url=f"/invoices/{invoice_id}?msg={msg}&msg_type=success", status_code=303)


@app.post("/invoices/{invoice_id}/payment")
async def invoice_payment(
    invoice_id: int,
    request: Request,
    payment_amount: float = Form(...),
    payment_date: str = Form(""),
    payment_notes: str = Form(""),
):
    """Record a payment against an invoice."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    try:
        p_date = datetime.strptime(payment_date, "%Y-%m-%d").date() if payment_date else None
    except ValueError:
        p_date = None

    try:
        invoice_generator.record_payment(invoice_id, payment_amount, p_date, payment_notes)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    msg = quote(f"Payment of ${payment_amount:.2f} recorded")
    return RedirectResponse(url=f"/invoices/{invoice_id}?msg={msg}&msg_type=success", status_code=303)


@app.post("/invoices/{invoice_id}/void")
async def invoice_void(invoice_id: int, request: Request, reason: str = Form("")):
    """Void an invoice."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    try:
        invoice_generator.void_invoice(invoice_id, reason)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    msg = quote("Invoice voided")
    return RedirectResponse(url=f"/invoices/{invoice_id}?msg={msg}&msg_type=warning", status_code=303)


@app.post("/invoices/{invoice_id}/reminder")
async def invoice_reminder(invoice_id: int, request: Request):
    """Generate a reminder PDF for an overdue invoice."""
    invoice_generator = getattr(request.app.state, "invoice_generator", None)
    if not invoice_generator:
        return HTMLResponse("Invoice generator not configured", status_code=500)

    try:
        pdf_path = invoice_generator.generate_reminder_pdf(invoice_id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)

    return FileResponse(
        path=pdf_path,
        filename=Path(pdf_path).name,
        media_type="application/pdf",
    )


# === Jobs ===

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    """Scheduled jobs status page."""
    scheduler = getattr(request.app.state, "scheduler", None)
    jobs = scheduler.get_jobs_status() if scheduler else []
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": jobs,
    })


@app.get("/api/jobs-table", response_class=HTMLResponse)
async def api_jobs_table(request: Request):
    """HTMX partial: jobs status table."""
    scheduler = getattr(request.app.state, "scheduler", None)
    jobs = scheduler.get_jobs_status() if scheduler else []
    return templates.TemplateResponse("partials/jobs_table.html", {
        "request": request,
        "jobs": jobs,
    })


@app.post("/jobs/{job_id}/trigger")
async def trigger_job(job_id: str, request: Request):
    """Manually trigger a scheduled job."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        return HTMLResponse("Scheduler not running", status_code=500)

    triggered = scheduler.trigger_job(job_id)
    if not triggered:
        msg = quote(f"Job '{job_id}' not found")
        return RedirectResponse(url=f"/jobs?msg={msg}&msg_type=error", status_code=303)

    msg = quote(f"Job '{job_id}' triggered")

    # Support HTMX requests — return partial if HX-Request header present
    if request.headers.get("HX-Request"):
        import asyncio
        await asyncio.sleep(1)  # Give job a moment to run
        jobs = scheduler.get_jobs_status()
        return templates.TemplateResponse("partials/jobs_table.html", {
            "request": request,
            "jobs": jobs,
        })

    return RedirectResponse(url=f"/jobs?msg={msg}&msg_type=success", status_code=303)


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
