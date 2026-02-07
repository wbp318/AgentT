"""
FastAPI web dashboard for AgentT.
Provides document monitoring, approval workflows, and system status.
"""

import logging
from pathlib import Path
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database.db import get_db_session
from database.models import Document, Entity, ApprovalRequest, AuditLog, DocumentStatus, ApprovalStatus

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent

app = FastAPI(title="AgentT", description="Farm Office Automation Agent")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")


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

    stats = {
        "total_documents": db.query(Document).count(),
        "pending_documents": db.query(Document).filter(
            Document.status.in_([DocumentStatus.PENDING, DocumentStatus.OCR_COMPLETE, DocumentStatus.CLASSIFIED])
        ).count(),
        "filed_documents": db.query(Document).filter(Document.status == DocumentStatus.FILED).count(),
        "error_documents": db.query(Document).filter(Document.status == DocumentStatus.ERROR).count(),
        "pending_approvals": len(pending_approvals),
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
    }
