"""
Central configuration for AgentT.
Loads from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base directory (project root)
BASE_DIR = Path(__file__).resolve().parent.parent

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'agent_t.db'}")

# Anthropic API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLASSIFICATION_MODEL = os.getenv("CLASSIFICATION_MODEL", "claude-sonnet-4-5-20250929")
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "claude-sonnet-4-5-20250929")

# Scanner
SCANNER_WATCH_DIR = Path(os.getenv("SCANNER_WATCH_DIR", str(BASE_DIR / "data" / "scanned")))
PROCESSED_DIR = BASE_DIR / "data" / "processed"
FILED_DIR = BASE_DIR / "data" / "filed"
EXPORTS_DIR = BASE_DIR / "data" / "exports"
INVOICES_DIR = BASE_DIR / "data" / "invoices"
BACKUP_DIR = BASE_DIR / "data" / "backups"

# Web dashboard
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = BASE_DIR / "logs"

# OCR
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")  # Leave empty to use PATH default

# Ensure directories exist
for d in [SCANNER_WATCH_DIR, PROCESSED_DIR, FILED_DIR, EXPORTS_DIR,
          INVOICES_DIR, BACKUP_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
