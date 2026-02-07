"""
OCR pipeline for scanned documents.
Uses Tesseract as the primary engine, with Claude Vision API as fallback
for low-confidence or complex documents.
"""

import logging
from pathlib import Path
from datetime import datetime

from core.events import EventBus, Event, FILE_ARRIVED, OCR_COMPLETE
from core.audit import log_action
from database.db import get_session
from database.models import Document, DocumentStatus

logger = logging.getLogger(__name__)


def _ocr_with_tesseract(file_path: Path) -> tuple[str, float]:
    """
    Run Tesseract OCR on a file. Returns (text, confidence).
    Handles both PDFs (via pdf2image) and images.
    """
    import pytesseract
    from config.settings import TESSERACT_CMD

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    if file_path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path
        images = convert_from_path(str(file_path))
        texts = []
        confidences = []
        for img in images:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            page_text = pytesseract.image_to_string(img)
            texts.append(page_text)
            # Average confidence of recognized words
            confs = [int(c) for c in data["conf"] if int(c) > 0]
            if confs:
                confidences.append(sum(confs) / len(confs))
        full_text = "\n\n--- PAGE BREAK ---\n\n".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, avg_conf / 100.0
    else:
        from PIL import Image
        img = Image.open(file_path)
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img)
        confs = [int(c) for c in data["conf"] if int(c) > 0]
        confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, confidence


def _ocr_with_claude_vision(file_path: Path) -> tuple[str, float]:
    """
    Fallback: use Claude's vision capability to extract text from a document image.
    Returns (text, confidence) where confidence is always high (Claude is reliable).
    """
    import anthropic
    import base64
    from config.settings import ANTHROPIC_API_KEY, EXTRACTION_MODEL

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Read and encode the file
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        # Convert first page to image for Claude
        from pdf2image import convert_from_path
        images = convert_from_path(str(file_path))
        from io import BytesIO
        buf = BytesIO()
        images[0].save(buf, format="PNG")
        image_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        media_type = "image/png"
    else:
        with open(file_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
            ".bmp": "image/bmp",
        }.get(suffix, "image/png")

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract ALL text from this document image. Preserve the layout and structure as much as possible. Return only the extracted text, nothing else.",
                },
            ],
        }],
    )

    text = response.content[0].text
    return text, 0.95  # Claude vision is high confidence


class OCRProcessor:
    """
    Processes documents through OCR.
    Listens for FILE_ARRIVED events, runs OCR, emits OCR_COMPLETE.
    """

    # Below this Tesseract confidence, fall back to Claude Vision
    CONFIDENCE_THRESHOLD = 0.60

    def setup(self, event_bus: EventBus):
        self._event_bus = event_bus
        event_bus.subscribe(FILE_ARRIVED, self.handle_file_arrived)

    def handle_file_arrived(self, event: Event):
        """Process a newly arrived file through OCR."""
        file_path = Path(event.data["file_path"])
        filename = event.data["filename"]

        logger.info(f"Starting OCR for: {filename}")

        # Create document record
        with get_session() as session:
            doc = Document(
                original_filename=filename,
                stored_path=str(file_path),
                scanned_at=datetime.utcnow(),
            )
            session.add(doc)
            session.flush()
            doc_id = doc.id

        # Try Tesseract first
        try:
            text, confidence = _ocr_with_tesseract(file_path)
            used_fallback = False

            if confidence < self.CONFIDENCE_THRESHOLD and text.strip():
                logger.info(f"Low Tesseract confidence ({confidence:.0%}), trying Claude Vision")
                try:
                    text, confidence = _ocr_with_claude_vision(file_path)
                    used_fallback = True
                except Exception as e:
                    logger.warning(f"Claude Vision fallback failed: {e}")
                    # Keep Tesseract result

        except Exception as e:
            logger.error(f"Tesseract OCR failed for {filename}: {e}")
            # Try Claude Vision as sole option
            try:
                text, confidence = _ocr_with_claude_vision(file_path)
                used_fallback = True
            except Exception as e2:
                logger.error(f"All OCR failed for {filename}: {e2}")
                with get_session() as session:
                    doc = session.get(Document, doc_id)
                    doc.status = DocumentStatus.ERROR
                    doc.error_message = f"OCR failed: {e}; Vision fallback: {e2}"
                log_action("scanner", "ocr_failed", detail={"filename": filename, "error": str(e)}, severity="error")
                return

        # Update document with OCR results
        with get_session() as session:
            doc = session.get(Document, doc_id)
            doc.ocr_text = text
            doc.ocr_confidence = confidence
            doc.status = DocumentStatus.OCR_COMPLETE
            doc.processed_at = datetime.utcnow()

        log_action("scanner", "ocr_complete", detail={
            "filename": filename,
            "confidence": round(confidence, 2),
            "used_fallback": used_fallback,
            "text_length": len(text),
            "doc_id": doc_id,
        })

        logger.info(f"OCR complete for {filename} (confidence: {confidence:.0%})")

        # Emit event for next stage
        self._event_bus.emit(Event(OCR_COMPLETE, {
            "doc_id": doc_id,
            "file_path": str(file_path),
            "filename": filename,
            "text": text,
            "confidence": confidence,
        }))
