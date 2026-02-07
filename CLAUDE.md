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

## Architecture

### Event-Driven Pipeline

The system is built around a synchronous event bus (`core/events.py`). Modules subscribe to events and emit new ones, forming a processing chain:

```
FILE_ARRIVED → OCR_COMPLETE → DOCUMENT_CLASSIFIED → DATA_EXTRACTED → DOCUMENT_FILED
```

A scanned PDF triggers this entire chain automatically. Each event carries a `data` dict with `doc_id`, `file_path`, `filename`, and stage-specific fields (e.g., `text`, `confidence`, `document_type`, `extracted_data`).

### Module Contract

Every module follows the same interface pattern for the `AgentT` orchestrator (`core/agent.py`):
- `setup(event_bus)` — subscribe to events (called on `agent.register_module()`)
- `start()` — begin active work (optional, only ScannerWatcher uses this)
- `stop()` — clean shutdown (optional)

Modules are registered in `main.py` and wired together through the event bus — they never import each other directly.

### Document Lifecycle

Documents progress through statuses defined in `database/models.py:DocumentStatus`:
`PENDING → OCR_COMPLETE → CLASSIFIED → EXTRACTED → FILED` (or `ERROR` at any stage)

The `Document` model accumulates data at each stage: `ocr_text` and `ocr_confidence` from OCR, `document_type` and `classification_confidence` from classification, `extracted_data` (JSON) from extraction, and `stored_path` + `filed_at` from filing.

### Multi-Entity Design

Three business entities are defined in `config/entities.py` (generic placeholders). Every database table that holds business data has an `entity_id` FK to `entities`. Entity resolution happens in two ways:
1. Keyword matching in `core/entity_context.py` (fast, for obvious cases)
2. Claude API classification in `modules/scanner/classifier.py` (for ambiguous documents)

### OCR Strategy

`modules/scanner/ocr.py` uses a two-tier approach:
1. **Tesseract** (free, local) runs first on all documents
2. **Claude Vision API** is called as fallback when Tesseract confidence < 60%

If Tesseract fails entirely, Claude Vision is tried as the sole engine.

### Database

SQLite via SQLAlchemy 2.0 with `DeclarativeBase`. Two session patterns:
- `get_session()` — context manager for CLI/module code
- `get_db_session()` — generator for FastAPI `Depends()` injection

All models are in `database/models.py`. No migrations yet (uses `create_all()`).

### Web Dashboard

FastAPI + Jinja2 templates + HTMX. Binds to `127.0.0.1:8080` by default. Templates are in `web/templates/` and extend `base.html`. The dashboard auto-refreshes stats via HTMX polling (`hx-trigger="every 30s"`).

### Audit Logging

`core/audit.py:log_action()` writes to both the `audit_log` database table and `logs/audit.log` file. Every module action should call this. The audit log is append-only by design (7-year IRS retention).

## Configuration

All config loads from `.env` via `config/settings.py`. Key variables:
- `ANTHROPIC_API_KEY` — required for classification/extraction
- `SCANNER_WATCH_DIR` — where the physical scanner saves PDFs
- `CLASSIFICATION_MODEL` / `EXTRACTION_MODEL` — Claude model IDs
- `TESSERACT_CMD` — path to Tesseract binary (leave empty to use PATH)

Entity definitions (names, types, states, keywords) are in `config/entities.py`.

## Related Projects

- `../tax_assistant/` — Schedule F expense/income categories in `config/tax_constants.py` (24 expense + 10 income categories reused here in `config/entities.py`)
- `../agtools/` — `jd_ops_cleaner.py` parses John Deere Operations Center exports; `cost_flag_report.py` has farm chemical product prices (`PRODUCT_PRICES` dict)
- `../fsa_sdrp/` — FSA SDRP entity data (Parker Farms Partnership, New Generation Farms, Westco Partnership II)

## Planned Phases

Phase 1 (foundation + scanner) is complete. Upcoming:
- **Phase 2**: QuickBooks IIF file generation + expense categorization + approval workflow
- **Phase 3**: Billing & invoice generation
- **Phase 4**: Dashboard polish + APScheduler task automation
- **Phase 5**: FSA/USDA crop reporting module
- **Phase 6**: Live QB sync via Conductor.is
