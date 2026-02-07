"""
Document manager — auto-files processed documents into organized folders.
Structure: data/filed/{entity_slug}/{document_type}/{YYYY-MM}/{filename}
"""

import logging
import shutil
from pathlib import Path
from datetime import datetime

from core.events import EventBus, Event, DATA_EXTRACTED, DOCUMENT_FILED
from core.audit import log_action
from database.db import get_session
from database.models import Document, DocumentStatus
from config.settings import FILED_DIR

logger = logging.getLogger(__name__)


def _build_filed_path(entity_slug: str | None, doc_type: str, original_filename: str) -> Path:
    """
    Build the destination path for a filed document.
    Structure: data/filed/{entity}/{doc_type}/{YYYY-MM}/{filename}
    """
    entity_dir = entity_slug or "unassigned"
    month_dir = datetime.now().strftime("%Y-%m")

    # Clean the filename
    safe_name = original_filename.replace(" ", "_")

    dest_dir = FILED_DIR / entity_dir / doc_type / month_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / safe_name

    # Handle duplicates
    if dest_path.exists():
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    return dest_path


class DocumentManager:
    """
    Manages document filing and organization.
    Listens for DATA_EXTRACTED events, copies files to organized locations.
    """

    def setup(self, event_bus: EventBus):
        self._event_bus = event_bus
        event_bus.subscribe(DATA_EXTRACTED, self.handle_data_extracted)

    def handle_data_extracted(self, event: Event):
        """File a document after data extraction is complete."""
        doc_id = event.data["doc_id"]
        file_path = Path(event.data["file_path"])
        filename = event.data["filename"]
        doc_type = event.data["document_type"]
        entity_slug = event.data.get("entity_slug")

        logger.info(f"Filing document: {filename}")

        # Build destination path
        dest_path = _build_filed_path(entity_slug, doc_type, filename)

        # Copy file to filed location (keep original in scanned for safety)
        try:
            shutil.copy2(str(file_path), str(dest_path))
        except Exception as e:
            logger.error(f"Failed to file {filename}: {e}")
            log_action("documents", "filing_failed", detail={
                "doc_id": doc_id,
                "filename": filename,
                "error": str(e),
            }, severity="error")
            return

        # Update document record
        with get_session() as session:
            doc = session.get(Document, doc_id)
            doc.stored_path = str(dest_path)
            doc.status = DocumentStatus.FILED
            doc.filed_at = datetime.utcnow()

        log_action("documents", "document_filed", detail={
            "doc_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "entity": entity_slug or "unassigned",
            "destination": str(dest_path),
        })

        logger.info(f"Filed {filename} -> {dest_path}")

        # Emit event
        self._event_bus.emit(Event(DOCUMENT_FILED, {
            "doc_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "entity_slug": entity_slug,
            "filed_path": str(dest_path),
        }))
