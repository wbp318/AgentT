# CLAUDE.md

## Project Overview

AgentT is a farm office automation agent for a multi-entity agricultural operation (two row crop farms in Louisiana + a Georgia real estate operation). It runs locally on the office PC alongside QuickBooks Desktop.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the agent (starts scanner + web dashboard)
python main.py run

# Run web dashboard only
python main.py web

# Run scanner only
python main.py scan

# Initialize database
python main.py init-db

# Run tests
pytest tests/
```

## Architecture

- `config/` — Settings, entity definitions, QB account mappings
- `database/` — SQLAlchemy models and DB session management
- `core/` — Agent orchestrator, event bus, audit logging, entity context, approval engine
- `modules/scanner/` — File watcher, OCR, document classification/extraction via Claude API
- `modules/documents/` — Document filing and organization
- `modules/quickbooks/` — IIF generation, expense categorization, QB CSV handling
- `modules/billing/` — Invoice generation, payment tracking
- `modules/fsa/` — FSA/USDA crop reporting, program tracking
- `web/` — FastAPI dashboard with HTMX for approvals and monitoring
- `data/` — Local data storage (scanned, processed, filed, exports)

## Key Patterns

- Every database table has an `entity_id` FK for multi-entity separation
- Event-driven: scanner emits events, modules subscribe and react
- Three approval tiers: Auto (filing), Review (categorization), Approve (QB entries)
- Claude API used for document classification and structured data extraction
- QuickBooks Desktop integration via IIF file generation (Phase 1)

## Related Projects

- `../tax_assistant/` — Tax calculation engine (shares entity/transaction patterns)
- `../agtools/` — Farm optimization tools (JD Operations data, product prices)
- `../fsa_sdrp/` — FSA SDRP documentation (entity data, crop info)
