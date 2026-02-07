"""
Simple event bus for inter-module communication.
Modules register handlers for events they care about.
"""

import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class Event:
    """Base event with a name and data payload."""

    def __init__(self, name: str, data: dict = None):
        self.name = name
        self.data = data or {}

    def __repr__(self):
        return f"<Event(name='{self.name}')>"


# Standard event names
FILE_ARRIVED = "file_arrived"
OCR_COMPLETE = "ocr_complete"
DOCUMENT_CLASSIFIED = "document_classified"
DATA_EXTRACTED = "data_extracted"
DOCUMENT_FILED = "document_filed"
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_DECIDED = "approval_decided"
TRANSACTION_CREATED = "transaction_created"
IIF_GENERATED = "iif_generated"
INVOICE_CREATED = "invoice_created"
ERROR_OCCURRED = "error_occurred"


class EventBus:
    """Synchronous event bus. Modules subscribe to event names and get called when events fire."""

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: Callable):
        """Register a handler for an event type."""
        self._handlers[event_name].append(handler)
        logger.debug(f"Handler {handler.__name__} subscribed to '{event_name}'")

    def emit(self, event: Event):
        """Fire an event. All registered handlers are called in order."""
        handlers = self._handlers.get(event.name, [])
        logger.info(f"Event '{event.name}' fired, {len(handlers)} handler(s)")
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Handler {handler.__name__} failed on '{event.name}': {e}")
                # Emit an error event (but don't recurse)
                if event.name != ERROR_OCCURRED:
                    self.emit(Event(ERROR_OCCURRED, {
                        "original_event": event.name,
                        "handler": handler.__name__,
                        "error": str(e),
                    }))
