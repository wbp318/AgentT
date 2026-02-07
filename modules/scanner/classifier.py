"""
Document classifier using Claude API.
Determines what type of document was scanned (invoice, receipt, lease, etc.)
and which entity it belongs to.
"""

import json
import logging
from datetime import datetime

import anthropic

from core.events import EventBus, Event, OCR_COMPLETE, DOCUMENT_CLASSIFIED
from core.audit import log_action
from database.db import get_session
from database.models import Document, DocumentStatus, DocumentType
from config.settings import ANTHROPIC_API_KEY, CLASSIFICATION_MODEL
from config.entities import DOCUMENT_TYPES, ENTITIES

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """You are a document classifier for a farm office. Analyze the following OCR text from a scanned document and determine:

1. **document_type**: One of: {doc_types}
2. **entity_slug**: Which business entity this document belongs to. Options: {entity_options}. Use null if you cannot determine.
3. **confidence**: Your confidence in the classification (0.0 to 1.0)
4. **summary**: A brief one-line summary of what this document is

The business entities are:
{entity_descriptions}

Respond with ONLY valid JSON in this exact format:
{{
    "document_type": "...",
    "entity_slug": "..." or null,
    "confidence": 0.0,
    "summary": "..."
}}

--- DOCUMENT TEXT ---
{text}
"""


class DocumentClassifier:
    """
    Classifies scanned documents using Claude API.
    Listens for OCR_COMPLETE events, classifies, emits DOCUMENT_CLASSIFIED.
    """

    def setup(self, event_bus: EventBus):
        self._event_bus = event_bus
        event_bus.subscribe(OCR_COMPLETE, self.handle_ocr_complete)

    def handle_ocr_complete(self, event: Event):
        """Classify a document after OCR is done."""
        doc_id = event.data["doc_id"]
        text = event.data["text"]
        filename = event.data["filename"]

        if not text.strip():
            logger.warning(f"Empty OCR text for {filename}, marking as unknown")
            with get_session() as session:
                doc = session.get(Document, doc_id)
                doc.document_type = DocumentType.UNKNOWN
                doc.status = DocumentStatus.CLASSIFIED
                doc.classification_confidence = 0.0
            return

        logger.info(f"Classifying document: {filename}")

        # Build entity descriptions for the prompt
        entity_options = list(ENTITIES.keys())
        entity_descriptions = "\n".join(
            f"- {slug}: {cfg['name']} ({cfg['entity_type']}, {cfg['state']})"
            for slug, cfg in ENTITIES.items()
        )

        prompt = CLASSIFICATION_PROMPT.format(
            doc_types=", ".join(DOCUMENT_TYPES),
            entity_options=", ".join(entity_options),
            entity_descriptions=entity_descriptions,
            text=text[:8000],  # Limit text to avoid token overuse
        )

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=CLASSIFICATION_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = response.content[0].text.strip()
            # Parse JSON from response (handle markdown code blocks)
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result = json.loads(result_text)

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.error(f"Failed to parse classification response: {e}")
            result = {
                "document_type": "unknown",
                "entity_slug": None,
                "confidence": 0.0,
                "summary": "Classification failed",
            }
        except anthropic.APIError as e:
            logger.error(f"Claude API error during classification: {e}")
            result = {
                "document_type": "unknown",
                "entity_slug": None,
                "confidence": 0.0,
                "summary": f"API error: {e}",
            }

        # Map to enum
        doc_type_str = result.get("document_type", "unknown")
        try:
            doc_type = DocumentType(doc_type_str)
        except ValueError:
            doc_type = DocumentType.UNKNOWN

        entity_slug = result.get("entity_slug")
        confidence = result.get("confidence", 0.0)

        # Update document record
        with get_session() as session:
            doc = session.get(Document, doc_id)
            doc.document_type = doc_type
            doc.classification_confidence = confidence
            doc.status = DocumentStatus.CLASSIFIED

            # Resolve entity
            if entity_slug:
                from core.entity_context import get_entity_by_slug
                entity = get_entity_by_slug(session, entity_slug)
                if entity:
                    doc.entity_id = entity.id

        log_action("scanner", "document_classified", detail={
            "doc_id": doc_id,
            "filename": filename,
            "document_type": doc_type_str,
            "entity_slug": entity_slug,
            "confidence": confidence,
            "summary": result.get("summary", ""),
        })

        logger.info(f"Classified {filename} as '{doc_type_str}' (confidence: {confidence:.0%})")

        # Emit event for extraction
        self._event_bus.emit(Event(DOCUMENT_CLASSIFIED, {
            "doc_id": doc_id,
            "file_path": event.data["file_path"],
            "filename": filename,
            "text": text,
            "document_type": doc_type_str,
            "entity_slug": entity_slug,
            "summary": result.get("summary", ""),
        }))
