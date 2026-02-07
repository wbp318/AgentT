# AgentT

Farm office automation agent for multi-entity agricultural operations. Automates document scanning, OCR, classification, QuickBooks bookkeeping, invoicing, and FSA/USDA paperwork.

## What It Does

Drop a document in the scanner folder and AgentT handles the rest:

1. **Detects** new PDFs/images via file watcher
2. **OCRs** the document (Tesseract, with Claude Vision fallback)
3. **Classifies** it (invoice, receipt, lease, FSA form, etc.) via Claude API
4. **Extracts** structured data (vendor, date, amounts, line items)
5. **Files** it into organized folders by entity/type/month
6. **Queues** QuickBooks entries for approval (Phase 2)

All actions are logged to an immutable audit trail. A web dashboard at `localhost:8080` shows document status, pending approvals, and system activity.

## Quick Start

```bash
# Clone
git clone https://github.com/wbp318/AgentT.git
cd AgentT

# Set up environment
python -m venv venv
.\venv\Scripts\activate        # Windows
pip install -r requirements.txt

# Configure
copy .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY and SCANNER_WATCH_DIR

# Initialize database
python main.py init-db

# Run (scanner + dashboard)
python main.py run
```

## Requirements

- **Python 3.13+**
- **Tesseract OCR** — install from [github.com/tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract) (or set `TESSERACT_CMD` in `.env`)
- **Poppler** — required by `pdf2image` for PDF-to-image conversion ([poppler releases](https://github.com/osber/poppler-windows/releases))
- **Anthropic API key** — for document classification and data extraction

## CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py run` | Start scanner watcher + web dashboard |
| `python main.py web` | Web dashboard only |
| `python main.py scan` | Scanner watcher only |
| `python main.py init-db` | Create database tables and seed entities |
| `python main.py status` | Show system status |

## Multi-Entity Support

AgentT manages three business entities from a single agent:
- Two Louisiana row crop farms
- One Georgia real estate operation

Every document, transaction, and invoice is tagged to an entity. Configure entity names and details in `config/entities.py`.

## Roadmap

See [docs/PLAN.md](docs/PLAN.md) for the full implementation plan.

- [x] **Phase 1**: Foundation + scanner/OCR pipeline
- [ ] **Phase 2**: QuickBooks IIF file generation + approval workflow
- [ ] **Phase 3**: Billing & invoice generation
- [ ] **Phase 4**: Dashboard polish + task scheduler
- [ ] **Phase 5**: FSA/USDA crop reporting module
- [ ] **Phase 6**: Live QB sync via Conductor.is

## License

MIT License - feel free to use this for your farm!

## Contact

Created by [@wbp318](https://github.com/wbp318)
