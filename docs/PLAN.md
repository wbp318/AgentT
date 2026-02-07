# AgentT — Farm Office Automation Agent

## Context

Two row crop farms (Louisiana) and a Georgia real estate operation share one office. Nearly all bookkeeping runs through QuickBooks Desktop. Documents pile up from the scanner, FSA deadlines sneak up, and invoices are managed manually. AgentT will be a locally-running Python agent that automates the repetitive office work — scanning/OCR, expense categorization, QB data entry, invoicing, document filing, and FSA tracking — with a web dashboard for oversight and approvals.

## Architecture

```
  Web Dashboard (FastAPI, port 8080)
          |
    Agent Core (orchestrator + event bus)
     /    |     |      \       \
Scanner  Docs   QB    Billing   FSA
(OCR)   (file) (IIF)  (PDF)   (crops)
  |       |      |       |       |
Tesseract SQLite QB      Jinja2  Form
+Claude   DB    Desktop  +WKHTML templates
```

- **Event-driven**: Scanner detects file -> OCR -> classify -> extract -> route to QB/Billing/Filing
- **Three approval tiers**: Auto (filing, OCR), Review (categorization), Approve (QB entries, invoice sends)
- **Multi-entity**: Every table has `entity_id` FK — clean separation, shared agent
- **JD Operations integration**: Import John Deere Operations Center exports (spray apps, fuel, field data) using the existing `agtools/jd_ops_cleaner.py` parser. Product costs from `agtools/cost_flag_report.py` feed into expense tracking.

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| Language | Python 3.13 (already installed) | Matches all existing projects |
| Web | FastAPI + Jinja2 + HTMX | Lightweight, async, interactive dashboard without JS framework |
| Database | SQLite + SQLAlchemy 2.0 + Alembic | Proven pattern from `tax_assistant` |
| AI | Claude API (Sonnet for categorization, Vision for OCR fallback) | Document understanding, expense classification |
| OCR | Tesseract via pytesseract | Free, local, handles clean scans well |
| File watching | watchdog | Monitors scanner output folder |
| Scheduling | APScheduler | Cron jobs, persisted to SQLite |
| PDF gen | WeasyPrint + Jinja2 | Invoice generation |
| QB integration | IIF files (Phase 1), Conductor.is (Phase 2, $49/mo) | IIF is free and immediate; Conductor adds live sync later |

**Estimated monthly cost**: $20-40/mo (Claude API only) in Phase 1, up to ~$90/mo with Conductor later.

## Implementation Phases

### Phase 1: Foundation + Scanner Module -- COMPLETE
- Project scaffolding, config, database models, core agent engine
- Scanner: watchdog watcher, Tesseract + Claude Vision OCR, classifier, extractor
- Document manager: auto-filing into entity/type/month folders
- FastAPI dashboard with HTMX, Click CLI

### Phase 2: QuickBooks IIF Integration -- NEXT
- `config/qb_accounts.py` — maps Schedule F categories to QB account names
- `modules/quickbooks/iif_generator.py` — IIF file creation (bills, deposits, vendors)
- `modules/quickbooks/categorizer.py` — Claude-powered expense categorization
- `modules/quickbooks/csv_handler.py` — read QB CSV exports
- `core/approval.py` — approval workflow engine + web UI
- `web/routes/approvals.py` — approval queue dashboard

### Phase 3: Billing & Invoicing
- `modules/billing/invoice_generator.py` — PDF invoices with entity branding
- `modules/billing/payment_tracker.py` — payment status + aging
- `modules/billing/reminders.py` — automated reminder generation
- `modules/billing/templates/` — Jinja2 HTML invoice templates

### Phase 4: Full Dashboard + Scheduler
- `core/scheduler.py` — APScheduler with cron jobs
- Full dashboard: entity switcher, activity feed, document search, approval queue
- `modules/quickbooks/reconciliation.py` — bank reconciliation prep
- `modules/quickbooks/reports.py` — QB report parsing

### Phase 5: FSA/USDA Module
- `modules/fsa/crop_reporting.py`
- `modules/fsa/program_tracker.py` — deadline calendar (SDRP, ARC, PLC)
- `modules/fsa/compliance.py`

### Phase 6: Live QB Sync (Conductor.is)
- `modules/quickbooks/qb_connector.py` — Conductor SDK integration ($49/mo per company file)

## Key Data Models

```
Entity:       id, name, slug, entity_type, state, accounting_method, qb_company_file, active
Document:     id, entity_id, original_filename, stored_path, document_type, ocr_text, extracted_data(JSON), status
Transaction:  id, entity_id, document_id, date, vendor, amount, category, qb_account, qb_sync_status, approval_id
Invoice:      id, entity_id, invoice_number, customer, date_issued, date_due, line_items(JSON), total, status
Approval:     id, entity_id, request_type, action_description, data_payload(JSON), status
AuditLog:     id, timestamp, entity_id, module, action, detail(JSON), severity
```

## QuickBooks Desktop Strategy

**Phase 1 (IIF)**: Generate tab-separated IIF files that QB imports natively via File > Utilities > Import. Write-only, zero cost, proven format. Agent generates the file, user drags into QB (5 seconds).

**Phase 2+ (Conductor.is)**: $49/mo per company file. Typed Python SDK, real-time read/write, eliminates manual import. Evaluate after IIF workflow is validated.

## Files to Reuse From Existing Projects

- `tax_assistant/config/tax_constants.py` — Schedule F expense/income categories (24 expense + 10 income)
- `tax_assistant/database/models.py` — Entity/Transaction ORM pattern
- `agtools/jd_ops_cleaner.py` — JD Operations Center Excel parser
- `agtools/cost_flag_report.py` — Product pricing dictionary (PRODUCT_PRICES)
- `agtools/src/agtools/farm_optimizer.py` — FarmOptimizer class patterns
- `fsa_sdrp/` — Entity data and FSA domain context for Phase 5
