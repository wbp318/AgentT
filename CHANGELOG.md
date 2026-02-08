# Changelog

All notable changes to AgentT will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.0] - 2026-02-07

### Added — Phase 4: Dashboard Polish + Task Automation
- **Scheduler**: APScheduler `BackgroundScheduler` with 4 jobs (America/Chicago timezone)
  - `check_overdue` (daily 7 AM) — flips SENT invoices past due date to OVERDUE
  - `database_backup` (daily 2 AM) — copies agent_t.db to `data/backups/`, prunes to 30
  - `scanner_sweep` (every 5 min) — emits FILE_ARRIVED for scanner files not yet in DB
  - `status_digest` (daily 6 PM) — appends pending/overdue/error counts to `logs/daily_digest.log`
- **Web**: Jobs status page (`/jobs`) with HTMX auto-refresh and manual "Run Now" buttons
- **Web**: Flash messages on all POST redirects via query params (`?msg=...&msg_type=success`)
- **Web**: Entity/status filter bars on documents, transactions, and invoices list pages
- **Web**: Entity column added to transactions table
- **Tests**: 17 new scheduler tests (67 total)

### Changed
- Vendor add route redirects with flash params instead of re-rendering template
- Inline flash divs removed from vendors template (now handled by base.html)

## [0.3.0] - 2026-02-07

### Added — Phase 3: Billing & Invoice Generation
- **Invoicing**: `InvoiceGenerator` module — create, PDF, payment, void, overdue, reminder
- **Invoicing**: Invoice lifecycle: DRAFT → SENT → PAID (or OVERDUE, VOID)
- **Invoicing**: Auto-sequential invoice numbering `{prefix}-{YYYY}-{NNN}` per entity per year
- **Invoicing**: WeasyPrint PDF generation with entity-branded letterhead templates
- **Invoicing**: Overdue reminder letter PDF generation with reminder count tracking
- **Web**: 11 invoice routes — list, create, detail, edit, PDF download, mark sent, record payment, void, reminder
- **Web**: Dashboard overdue invoice alerts with reminder buttons
- **Database**: Entity branding fields — address, phone, email, tax_id, logo_path, invoice_prefix
- **Tests**: 23 invoice generator tests (50 total)

## [0.2.0] - 2026-02-07

### Added — Phase 2: QuickBooks IIF + Approval Workflow
- **QuickBooks**: IIF file generation for BILL, CHECK, and DEPOSIT transaction types
- **QuickBooks**: Tab-separated format with CRLF endings, TRNS+SPL balanced to zero
- **QuickBooks**: Per-entity IIF output directories (`data/exports/iif/{entity_slug}/{YYYY-MM}/`)
- **Approval**: Approval workflow engine — create, decide (approve/reject), pending queue
- **Approval**: APPROVAL_DECIDED event triggers automatic IIF generation
- **Categorizer**: Expense categorization via vendor mapping table → Claude API fallback
- **Categorizer**: 36 seeded vendor-to-Schedule F category defaults
- **Categorizer**: Vendor learning — save new mappings from web UI or API
- **Web**: Transaction creation form pre-filled from document extracted data
- **Web**: HTMX-powered real-time category suggestion on vendor name input
- **Web**: Approval queue with approve/reject actions
- **Web**: Transaction list with IIF download and "Mark Synced" actions
- **Web**: Vendor mapping management page
- **Database**: VendorMapping model, IIFType enum, iif_type column on Transaction
- **Tests**: 27 tests — approval (8), categorizer (7), IIF generator (12)

## [0.1.0] - 2026-02-07

### Added — Phase 1: Foundation + Scanner Module
- **Core**: Event-driven agent orchestrator with synchronous event bus
- **Core**: Audit logging (dual-write to SQLite + file) for 7-year IRS retention
- **Database**: SQLite via SQLAlchemy 2.0 with 6 tables (Entity, Document, Transaction, Invoice, Approval, AuditLog) and 8 enums
- **Config**: Multi-entity support for 3 business entities (2 LA row crop farms + 1 GA real estate)
- **Config**: Schedule F expense/income categories (24 expense + 10 income)
- **Scanner**: Watchdog-based file watcher on scanner output folder (2s write-delay)
- **OCR**: Two-tier OCR — Tesseract first, Claude Vision API fallback when confidence < 60%
- **OCR**: Multi-page PDF support via pdf2image with page break separators
- **Classifier**: Claude API document classification (invoice, receipt, lease, FSA form, etc.)
- **Extractor**: Claude API structured data extraction (vendor, date, amounts, line items)
- **Filing**: Auto-filing into `data/filed/{entity}/{type}/{YYYY-MM}/` (copy, not move)
- **Entity resolution**: Keyword matching + Claude API fallback for entity assignment
- **Web**: FastAPI + Jinja2 + HTMX dashboard at localhost:8080 (dark theme, no auth)
- **CLI**: Click commands — `run`, `web`, `scan`, `init-db`, `status` (with Rich output)
- **Error handling**: Per-document error isolation, ERROR_OCCURRED events, no cascading failures
