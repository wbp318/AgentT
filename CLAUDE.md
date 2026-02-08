# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentT is a farm office automation agent for a multi-entity agricultural operation (two Louisiana row crop farms + a Georgia real estate operation). It runs locally on the office PC alongside QuickBooks Desktop. The agent watches a scanner output folder, OCRs documents, classifies and extracts data via Claude API, auto-files them, generates QuickBooks IIF import files with an approval workflow, produces outbound invoices with PDF generation and payment tracking, and runs scheduled background jobs for overdue checking, database backups, scanner sweeps, and daily digest reports.

## Commands

```bash
python main.py init-db          # Create tables + seed entities + vendor mappings (run first)
python main.py run              # Start scanner watcher + web dashboard
python main.py web              # Web dashboard only (no scanner)
python main.py scan             # Scanner only (no dashboard)
python main.py status           # Show entity/document/transaction/approval/invoice counts
python main.py --log-level DEBUG run  # Verbose logging

pytest tests/                   # Run all tests (67 tests)
pytest tests/test_iif_generator.py -v          # Run one test file
pytest tests/test_approval.py -k "test_cannot" # Run a single test by name
```

CLI uses Click; output styled with Rich. All commands auto-initialize the DB if needed (idempotent).

## Architecture

### Event-Driven Pipeline

The system is built around a **synchronous** event bus (`core/events.py`). Modules subscribe to events and emit new ones, forming processing chains:

**Phase 1 — Document Processing:**
```
FILE_ARRIVED → OCR_COMPLETE → DOCUMENT_CLASSIFIED → DATA_EXTRACTED → DOCUMENT_FILED
```

**Phase 2 — QuickBooks Integration (user-initiated):**
```
User creates transaction via web UI → APPROVAL_REQUESTED → APPROVAL_DECIDED → IIF_GENERATED
```

Each event carries a `data` dict with `doc_id`, `file_path`, `filename`, and stage-specific fields. Because the bus is synchronous, all handlers run sequentially in the same thread. Event handlers each open their own `get_session()` — state is **not** shared between handlers. The `doc_id` in event data is the coordination key across stages.

**Phase 3 — Invoicing (user-initiated):**
```
User creates invoice via web UI → InvoiceGenerator.create_invoice() → DRAFT
  → mark_sent() → SENT → record_payment() → PAID
  → check_overdue() → OVERDUE → generate_reminder_pdf()
```

**Phase 4 — Scheduled Jobs (automatic):**
```
TaskScheduler.start() → BackgroundScheduler runs 4 jobs:
  check_overdue    (daily 7 AM CT)  → InvoiceGenerator.check_overdue()
  database_backup  (daily 2 AM CT)  → shutil.copy2 → data/backups/
  scanner_sweep    (every 5 min)    → emit FILE_ARRIVED for new files
  status_digest    (daily 6 PM CT)  → append to logs/daily_digest.log
```

### Module Contract

Every module follows the same interface pattern for the `AgentT` orchestrator (`core/agent.py`):
- `setup(event_bus)` — subscribe to events (called on `agent.register_module()`)
- `start()` — begin active work (ScannerWatcher and TaskScheduler use this)
- `stop()` — clean shutdown (optional)

Modules are registered in `main.py` and wired together through the event bus — they never import each other directly.

### Phase 2: QB Transaction Flow

Transaction creation is **manual** — user clicks "Create Transaction" on a filed document in the web UI. The flow:
1. Web form pre-fills from `extracted_data` (vendor, date, amount, ref#)
2. `ExpenseCategorizer` suggests category via vendor mapping table → Claude API fallback
3. User edits fields, selects IIF type (BILL/CHECK/DEPOSIT), submits
4. `Transaction` created + `ApprovalRequest` (type=QB_ENTRY) created
5. User approves in the approval queue → `APPROVAL_DECIDED` event emitted
6. `IIFGenerator` subscribes to `APPROVAL_DECIDED`, auto-generates IIF file
7. User downloads IIF, imports into QuickBooks Desktop, marks as synced

Key services are accessed via `request.app.state` in web routes (categorizer, iif_generator, approval_engine, invoice_generator, scheduler, event_bus). These are wired in `main.py` during startup.

Each entity has a **separate QB company file** — IIF files go to `data/exports/iif/{entity_slug}/{YYYY-MM}/`.

### Phase 3: Invoice Flow

`modules/billing/invoice_generator.py` — `InvoiceGenerator` follows the module contract. Invoices are created manually via the web UI (farm entities only; GA Real Estate excluded for now).

**Invoice lifecycle:** `DRAFT → SENT → PAID` (or `OVERDUE` if past due, or `VOID` at any non-PAID stage)

Key methods:
- `create_invoice()` — auto-generates invoice number `{prefix}-{YYYY}-{NNN}`, calculates totals from line items, status=DRAFT
- `generate_pdf()` / `generate_reminder_pdf()` — renders Jinja2 HTML templates via WeasyPrint, saves to `data/invoices/{entity_slug}/{YYYY}/`
- `mark_sent()` — DRAFT→SENT transition
- `record_payment()` — adds to `amount_paid`; auto-sets PAID when `balance_due <= 0`
- `void_invoice()` — sets VOID (cannot void PAID invoices)
- `check_overdue()` — queries SENT invoices past `date_due`, flips to OVERDUE
- `update_invoice()` — edits DRAFT invoices only

**Entity branding:** Each entity has `address`, `phone`, `email`, `invoice_prefix` (and `tax_id`, `logo_path` for future use) stored in the `entities` table and populated from `config/entities.py` during `seed_entities()`. These appear on invoice and reminder PDFs.

**PDF templates:** `modules/billing/templates/invoice.html` (invoice) and `reminder.html` (past due notice). Print-friendly CSS, entity letterhead, line items table. WeasyPrint requires GTK/Pango libraries on Windows.

**Web routes (11):** CRUD for invoices at `/invoices/*` — list, create (GET/POST), detail, edit (GET/POST), PDF download, mark sent, record payment, void, generate reminder. Dashboard shows invoice stats and overdue alerts with reminder buttons.

### IIF File Format

IIF files are tab-separated with CRLF line endings, UTF-8 without BOM. Every transaction has:
- `!TRNS` / `!SPL` / `!ENDTRNS` header rows
- `TRNS` line + `SPL` line(s) that must balance to zero
- Three types: BILL (AP entry), CHECK (direct payment), DEPOSIT (income)

### Document Lifecycle

Documents progress through statuses defined in `database/models.py:DocumentStatus`:
`PENDING → OCR_COMPLETE → CLASSIFIED → EXTRACTED → FILED` (or `ERROR` at any stage)

Status transitions are strictly forward — no backwards transitions. ERROR is terminal.

### Multi-Entity Design

Three business entities in `config/entities.py` (generic placeholders; real names are in `fsa_sdrp/`). Each entity has branding fields (address, phone, email, invoice_prefix) used on invoices and reminder letters. Every database table that holds business data has an `entity_id` FK. Entity resolution:
1. Keyword matching in `core/entity_context.py` (fast, for obvious cases)
2. Claude API classification in `modules/scanner/classifier.py` (for ambiguous documents)

Entity `slug` (e.g., "farm_1") is the primary identifier used in config, file paths, event data, and API responses. `Entity.id` is only used for database foreign keys.

### OCR Strategy

`modules/scanner/ocr.py` uses a two-tier approach:
1. **Tesseract** (free, local) runs first on all documents
2. **Claude Vision API** is called as fallback when Tesseract confidence < 60%

For PDFs, `pdf2image` converts to images first. Multi-page PDFs produce text with `--- PAGE BREAK ---` separators. Claude Vision fallback only uses the first page.

### Document Filing

`modules/documents/manager.py` copies (not moves) processed documents to:
```
data/filed/{entity_slug}/{document_type}/{YYYY-MM}/{filename}
```
Originals stay in `data/scanned/` so documents can be re-processed.

### Database

SQLite via SQLAlchemy 2.0 with `DeclarativeBase`. Two session patterns:
- `get_session()` — context manager for CLI/module code
- `get_db_session()` — generator for FastAPI `Depends()` injection

**Important:** Entity and other ORM objects become detached after `get_session()` closes. Extract scalar data (name, slug, etc.) inside the session block before using outside it.

All models are in `database/models.py` (7 tables, 11 enums). Flexible data uses JSON columns (`extracted_data`, `line_items`, `data_payload`). No formal migrations — uses `create_all()` plus `_add_missing_columns()` in `database/db.py` which auto-adds new model columns to existing SQLite tables via `ALTER TABLE`.

### Web Dashboard

FastAPI + Jinja2 templates + HTMX (loaded from CDN, no build step). All routes are in `web/app.py`. Binds to `127.0.0.1:8080` by default. Templates extend `base.html`. Dark theme via inline CSS custom properties. No authentication (localhost-only, single-user).

**Phase 4 additions:** Flash messages via query params (`?msg=...&msg_type=success`), entity/status filter bars on documents/transactions/invoices list pages, entity column on transactions table, Jobs page (`/jobs`) with HTMX auto-refresh and manual "Run Now" buttons.

### Expense Categorization

`modules/quickbooks/categorizer.py` — `ExpenseCategorizer` is called as a service (not an event handler):
1. Checks `vendor_mappings` DB table via `config/qb_accounts.py:get_category_for_vendor()`
2. Falls back to Claude API with all Schedule F categories in the prompt
3. Vendor-to-category mappings can be learned via `learn_vendor()` or the web UI

`config/qb_accounts.py` contains QB account name mappings for all 34 Schedule F categories (24 expense + 10 income) and 36 seeded vendor defaults.

### Audit Logging

`core/audit.py:log_action()` writes to both the `audit_log` database table and `logs/audit.log` file. Dual-write is intentional redundancy — DB is queryable, file is immutable backup. Append-only by design (7-year IRS retention).

### Error Handling

Modules catch exceptions and emit `ERROR_OCCURRED` events. The agent subscribes to these and logs to audit trail. Documents get `DocumentStatus.ERROR` with `error_message`. Processing continues for other documents (no cascading failures).

## Key Dependencies

- **Web**: FastAPI, uvicorn, Jinja2
- **Database**: SQLAlchemy 2.0 (alembic installed but not yet used)
- **AI**: anthropic SDK
- **OCR**: pytesseract, Pillow, pdf2image, PyPDF2
- **File watching**: watchdog
- **CLI**: click, rich
- **Config**: python-dotenv
- **Testing**: pytest, pytest-asyncio
- **PDF generation**: weasyprint (requires GTK/Pango on Windows)
- **Scheduling**: APScheduler 3.x (BackgroundScheduler, Central time)
- **Installed but not yet used**: pandas

## Configuration

All config loads from `.env` via `config/settings.py`. Key variables:
- `ANTHROPIC_API_KEY` — required for classification/extraction/categorization
- `SCANNER_WATCH_DIR` — where the physical scanner saves PDFs
- `CLASSIFICATION_MODEL` / `EXTRACTION_MODEL` / `CATEGORIZATION_MODEL` — Claude model IDs
- `TESSERACT_CMD` — path to Tesseract binary (leave empty to use PATH)

`settings.py` auto-creates all data directories on import (including `IIF_OUTPUT_DIR`, `INVOICES_DIR`).

Entity definitions (names, types, states, keywords, crops, branding, invoice prefix) are in `config/entities.py`, along with Schedule F expense/income categories (24 expense + 10 income).

## Testing

67 tests across 5 files: `test_approval.py` (8), `test_categorizer.py` (7), `test_iif_generator.py` (12), `test_invoice_generator.py` (23), `test_scheduler.py` (17).

Tests use in-memory SQLite databases with mock `get_session` pattern:
```python
with patch("module.path.get_session") as mock_gs:
    mock_gs.return_value.__enter__ = lambda s: db_session
    mock_gs.return_value.__exit__ = MagicMock(return_value=False)
```

**Important:** The mock `get_session` does NOT auto-commit (unlike the real one). To verify state changes on ORM objects, use `db_session.get(Model, id)` instead of `db_session.refresh(obj)` — `refresh()` does a SELECT that discards uncommitted dirty state.

`datetime.utcnow()` deprecation warnings from SQLAlchemy model defaults are known and non-critical.

WeasyPrint tests mock `sys.modules["weasyprint"]` to avoid GTK/Pango dependency on Windows. PDF content is not tested; only that the generator calls WeasyPrint and updates DB records correctly.

## Related Projects

- `../tax_assistant/` — Schedule F expense/income categories in `config/tax_constants.py`
- `~/agtools/` (NOT `~/.agtools`, which is just a cache folder) — `jd_ops_cleaner.py` parses John Deere Operations Center exports; `cost_flag_report.py` has farm chemical product prices
- `../fsa_sdrp/` — FSA SDRP entity data (Parker Farms Partnership, New Generation Farms, Westco Partnership II)

## Planned Phases

Phases 1-4 are complete. Upcoming:
- **Phase 5**: FSA/USDA crop reporting module (deferred — research complete, implementation on hold)
- **Phase 6**: Live QB sync via Conductor.is
