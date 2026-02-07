"""
Structured data extractor using Claude API.
Pulls vendor names, dates, amounts, line items, etc. from document text.
"""

import json
import logging
from datetime import datetime

import anthropic

from core.events import EventBus, Event, DOCUMENT_CLASSIFIED, DATA_EXTRACTED
from core.audit import log_action
from database.db import get_session
from database.models import Document, DocumentStatus
from config.settings import ANTHROPIC_API_KEY, EXTRACTION_MODEL

logger = logging.getLogger(__name__)

EXTRACTION_PROMPTS = {
    "invoice": """Extract the following from this vendor invoice:
- vendor_name: The company or person who sent the invoice
- invoice_number: The invoice number
- invoice_date: Date of the invoice (YYYY-MM-DD)
- due_date: Payment due date (YYYY-MM-DD) or null
- line_items: Array of {{"description": "...", "quantity": 0, "unit_price": 0.00, "amount": 0.00}}
- subtotal: Subtotal before tax
- tax: Tax amount or 0
- total: Total amount due
- notes: Any additional notes or terms

Respond with ONLY valid JSON.""",

    "receipt": """Extract the following from this receipt:
- vendor_name: Store/vendor name
- date: Date of purchase (YYYY-MM-DD)
- items: Array of {{"description": "...", "amount": 0.00}}
- subtotal: Subtotal
- tax: Tax amount or 0
- total: Total paid
- payment_method: cash, check, card, or unknown

Respond with ONLY valid JSON.""",

    "bank_statement": """Extract the following from this bank statement:
- bank_name: Name of the bank
- account_number_last4: Last 4 digits of account number
- statement_period_start: Start date (YYYY-MM-DD)
- statement_period_end: End date (YYYY-MM-DD)
- beginning_balance: Starting balance
- ending_balance: Ending balance
- total_deposits: Total deposits
- total_withdrawals: Total withdrawals
- transaction_count: Number of transactions

Respond with ONLY valid JSON.""",

    "default": """Extract any structured data you can from this document:
- document_date: Date on the document (YYYY-MM-DD) or null
- parties: Array of names/companies mentioned
- amounts: Array of {{"description": "...", "amount": 0.00}}
- key_details: Object with any other important fields

Respond with ONLY valid JSON.""",
}


class DataExtractor:
    """
    Extracts structured data from classified documents using Claude API.
    Listens for DOCUMENT_CLASSIFIED events, extracts data, emits DATA_EXTRACTED.
    """

    def setup(self, event_bus: EventBus):
        self._event_bus = event_bus
        event_bus.subscribe(DOCUMENT_CLASSIFIED, self.handle_classified)

    def handle_classified(self, event: Event):
        """Extract structured data from a classified document."""
        doc_id = event.data["doc_id"]
        text = event.data["text"]
        filename = event.data["filename"]
        doc_type = event.data["document_type"]

        logger.info(f"Extracting data from {filename} (type: {doc_type})")

        # Pick the right extraction prompt
        prompt_template = EXTRACTION_PROMPTS.get(doc_type, EXTRACTION_PROMPTS["default"])
        prompt = f"{prompt_template}\n\n--- DOCUMENT TEXT ---\n{text[:8000]}"

        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=EXTRACTION_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            result_text = response.content[0].text.strip()
            # Parse JSON (handle markdown code blocks)
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            extracted = json.loads(result_text)

        except (json.JSONDecodeError, IndexError) as e:
            logger.error(f"Failed to parse extraction response for {filename}: {e}")
            extracted = {"error": "extraction_parse_failed", "raw": result_text[:500] if 'result_text' in dir() else ""}
        except anthropic.APIError as e:
            logger.error(f"Claude API error during extraction for {filename}: {e}")
            extracted = {"error": f"api_error: {e}"}

        # Update document
        with get_session() as session:
            doc = session.get(Document, doc_id)
            doc.extracted_data = extracted
            doc.status = DocumentStatus.EXTRACTED

        log_action("scanner", "data_extracted", detail={
            "doc_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "extracted_keys": list(extracted.keys()) if isinstance(extracted, dict) else [],
        })

        logger.info(f"Data extracted from {filename}")

        # Emit event for filing
        self._event_bus.emit(Event(DATA_EXTRACTED, {
            "doc_id": doc_id,
            "file_path": event.data["file_path"],
            "filename": filename,
            "document_type": doc_type,
            "entity_slug": event.data.get("entity_slug"),
            "extracted_data": extracted,
        }))
