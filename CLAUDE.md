# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentT is a farm office automation agent for a multi-entity agricultural operation (two Louisiana row crop farms + a Georgia real estate operation). It runs locally on the office PC alongside QuickBooks Desktop. The agent watches a scanner output folder, OCRs documents, classifies and extracts data via Claude API, auto-files them, and (in later phases) generates QuickBooks IIF import files.

## Commands

```bash
python main.py init-db          # Create tables + seed entities (run first)
python main.py run              # Start scanner watcher + web dashboard
python main.py web              # Web dashboard only (no scanner)
python main.py scan             # Scanner only (no dashboard)
python main.py status           # Show entity/document/approval counts
python main.py --log-level DEBUG run  # Verbose logging

pytest tests/                   # Run all tests
pytest tests/test_scanner.py -k "test_ocr"  # Run a single test
```

CLI uses Click; output styled with Rich. All commands auto-initialize the DB if needed (idempotent).

## Architecture

### Event-Driven Pipeline

The system is built around a **synchronous** event bus (`core/events.py`). Modules subscribe to events and emit new ones, forming a processing chain:

```
FILE_ARRIVED → OCR_COMPLETE → DOCUMENT_CLASSIFIED → DATA_EXTRACTED → DOCUMENT_FILED
```

A scanned PDF triggers this entire chain automatically. Each event carries a `data` dict with `doc_id`, `file_path`, `filename`, and stage-specific fields (e.g., `text`, `confidence`, `document_type`, `extracted_data`).

Because the bus is synchronous, all handlers run sequentially in the same thread. A slow handler (e.g., Claude API call) blocks subsequent handlers. This is acceptable because documents process one at a time through the pipeline.

Event handlers each open their own `get_session()` — state is **not** shared between handlers. The `doc_id` in event data is the coordination key across stages.

### Module Contract

Every module follows the same interface pattern for the `AgentT` orchestrator (`core/agent.py`):
- `setup(event_bus)` — subscribe to events (called on `agent.register_module()`)
- `start()` — begin active work (optional, only ScannerWatcher uses this)
- `stop()` — clean shutdown (optional)

Modules are registered in `main.py` and wired together through the event bus — they never import each other directly. Registration order doesn't affect event processing (subscriptions are order-independent).

### Document Lifecycle

Documents progress through statuses defined in `database/models.py:DocumentStatus`:
`PENDING → OCR_COMPLETE → CLASSIFIED → EXTRACTED → FILED` (or `ERROR` at any stage)

Status transitions are strictly forward — no backwards transitions. ERROR is terminal.

The `Document` model accumulates data at each stage: `ocr_text` and `ocr_confidence` from OCR, `document_type` and `classification_confidence` from classification, `extracted_data` (JSON) from extraction, and `stored_path` + `filed_at` from filing.

### Multi-Entity Design

Three business entities are defined in `config/entities.py` (generic placeholders; real names are in `fsa_sdrp/`). Every database table that holds business data has an `entity_id` FK to `entities`. Entity resolution happens in two ways:
1. Keyword matching in `core/entity_context.py` (fast, for obvious cases)
2. Claude API classification in `modules/scanner/classifier.py` (for ambiguous documents)

Entity `slug` (e.g., "farm_1") is the primary identifier used in config, file paths (`data/filed/farm_1/...`), event data, and API responses. `Entity.id` is only used for database foreign keys.

### OCR Strategy

`modules/scanner/ocr.py` uses a two-tier approach:
1. **Tesseract** (free, local) runs first on all documents
2. **Claude Vision API** is called as fallback when Tesseract confidence < 60%

If Tesseract fails entirely, Claude Vision is tried as the sole engine.

For PDFs, `pdf2image` converts to images first, then Tesseract OCRs each page. Multi-page PDFs produce text with `--- PAGE BREAK ---` separators. Claude Vision fallback only uses the first page.

### Document Filing

`modules/documents/manager.py` copies (not moves) processed documents to:
```
data/filed/{entity_slug}/{document_type}/{YYYY-MM}/{filename}
```
Originals stay in `data/scanned/` so documents can be re-processed. Filename collisions append `_1`, `_2`, etc.

### Claude API Usage

Classification and extraction prompts truncate document text to **8000 characters** to control costs and fit context windows. No rate limiting is currently implemented.

### Database

SQLite via SQLAlchemy 2.0 with `DeclarativeBase`. Two session patterns:
- `get_session()` — context manager for CLI/module code
- `get_db_session()` — generator for FastAPI `Depends()` injection

All models are in `database/models.py` (6 tables, 8 enums). Flexible data uses JSON columns (`extracted_data`, `line_items`, `data_payload`). No migrations yet (uses `create_all()`).

### Web Dashboard

FastAPI + Jinja2 templates + HTMX (loaded from CDN, no build step). All routes are in `web/app.py`. Binds to `127.0.0.1:8080` by default. Templates are in `web/templates/` and extend `base.html`. Dark theme via inline CSS custom properties. No authentication (localhost-only, single-user).

### Audit Logging

`core/audit.py:log_action()` writes to both the `audit_log` database table and `logs/audit.log` file. Dual-write is intentional redundancy — DB is queryable, file is immutable backup. Every module action should call this. Append-only by design (7-year IRS retention).

### Error Handling

Modules catch exceptions and emit `ERROR_OCCURRED` events. The agent subscribes to these and logs to audit trail. Documents get `DocumentStatus.ERROR` with `error_message`. Processing continues for other documents (no cascading failures).

### Scanner Watcher

`ScannerWatcher` waits **2 seconds** after file creation before emitting `FILE_ARRIVED`. This delay lets the scanner hardware finish writing the file.

## Key Dependencies

- **Web**: FastAPI, uvicorn, Jinja2
- **Database**: SQLAlchemy 2.0 (alembic installed but not yet used)
- **AI**: anthropic SDK
- **OCR**: pytesseract, Pillow, pdf2image, PyPDF2
- **File watching**: watchdog
- **CLI**: click, rich
- **Config**: python-dotenv
- **Testing**: pytest, pytest-asyncio
- **Installed but not yet used**: APScheduler (Phase 4), weasyprint (Phase 3), pandas

## Configuration

All config loads from `.env` via `config/settings.py`. Key variables:
- `ANTHROPIC_API_KEY` — required for classification/extraction
- `SCANNER_WATCH_DIR` — where the physical scanner saves PDFs
- `CLASSIFICATION_MODEL` / `EXTRACTION_MODEL` — Claude model IDs
- `TESSERACT_CMD` — path to Tesseract binary (leave empty to use PATH)

`settings.py` auto-creates all data directories on import.

Entity definitions (names, types, states, keywords, crops) are in `config/entities.py`, along with Schedule F expense/income categories (24 expense + 10 income) reused from `tax_assistant`.

## Related Projects

- `../tax_assistant/` — Schedule F expense/income categories in `config/tax_constants.py`
- `~/agtools/` (NOT `~/.agtools`, which is just a cache folder) — `jd_ops_cleaner.py` parses John Deere Operations Center exports; `cost_flag_report.py` has farm chemical product prices
- `../fsa_sdrp/` — FSA SDRP entity data (Parker Farms Partnership, New Generation Farms, Westco Partnership II)

## Planned Phases

Phase 1 (foundation + scanner) is complete. Upcoming:
- **Phase 2**: QuickBooks IIF file generation + expense categorization + approval workflow
- **Phase 3**: Billing & invoice generation
- **Phase 4**: Dashboard polish + APScheduler task automation
- **Phase 5**: FSA/USDA crop reporting module
- **Phase 6**: Live QB sync via Conductor.is
