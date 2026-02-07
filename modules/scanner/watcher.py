"""
File system watcher for the scanner output folder.
Detects new PDFs and images, emits FILE_ARRIVED events.
"""

import logging
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from core.events import EventBus, Event, FILE_ARRIVED
from config.settings import SCANNER_WATCH_DIR

logger = logging.getLogger(__name__)

# File extensions we process
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


class ScannerHandler(FileSystemEventHandler):
    """Handles new files appearing in the scanner folder."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.debug(f"Ignoring non-document file: {file_path.name}")
            return

        # Wait briefly for the file to finish writing (scanner may still be writing)
        time.sleep(2)

        logger.info(f"New document detected: {file_path.name}")
        self.event_bus.emit(Event(FILE_ARRIVED, {
            "file_path": str(file_path),
            "filename": file_path.name,
        }))


class ScannerWatcher:
    """
    Watches the scanner output directory for new documents.
    Register with the agent via setup() and start/stop the observer.
    """

    def __init__(self, watch_dir: Path = None):
        self.watch_dir = watch_dir or SCANNER_WATCH_DIR
        self._observer = None
        self._event_bus = None

    def setup(self, event_bus: EventBus):
        """Register with the event bus (called by agent.register_module)."""
        self._event_bus = event_bus

    def start(self):
        """Start watching the scanner folder."""
        if not self._event_bus:
            raise RuntimeError("ScannerWatcher not set up — call setup(event_bus) first")

        self.watch_dir.mkdir(parents=True, exist_ok=True)
        handler = ScannerHandler(self._event_bus)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.watch_dir), recursive=False)
        self._observer.start()
        logger.info(f"Watching for scanned documents in: {self.watch_dir}")

    def stop(self):
        """Stop the file watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            logger.info("Scanner watcher stopped")
