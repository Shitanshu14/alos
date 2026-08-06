"""
Week 4 — PDF/DOCX text extraction service.

Pulls plain text out of an uploaded chapter file so it can be handed to
the AI understanding step (ai_service.py). Kept as a plain function (not
a class) - it's a stateless transformation: file on disk in, text out.
"""
import logging
from pathlib import Path

from pypdf import PdfReader
from docx import Document

logger = logging.getLogger("app.services.text_extraction")


class TextExtractionError(Exception):
    """Raised when a chapter file's text cannot be extracted."""


def extract_text(file_path: str, file_type: str) -> str:
    """Extract plain text from a chapter file. file_type is 'pdf' or 'docx'
    (same value already stored on the Chapter row from the upload step)."""
    path = Path(file_path)
    if not path.exists():
        raise TextExtractionError(f"File not found on disk: {file_path}")

    file_type = file_type.lower()
    if file_type == "pdf":
        return _extract_pdf(path)
    elif file_type == "docx":
        return _extract_docx(path)
    else:
        raise TextExtractionError(f"Unsupported file type for extraction: {file_type}")


def _extract_pdf(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages_text).strip()
    except Exception as exc:
        logger.exception("Failed to extract text from PDF: %s", path)
        raise TextExtractionError(f"Could not read PDF: {exc}") from exc

    if not text:
        raise TextExtractionError(
            "No extractable text found in PDF (it may be a scanned/image-only file)"
        )
    return text


def _extract_docx(path: Path) -> str:
    try:
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs).strip()
    except Exception as exc:
        logger.exception("Failed to extract text from DOCX: %s", path)
        raise TextExtractionError(f"Could not read DOCX: {exc}") from exc

    if not text:
        raise TextExtractionError("No extractable text found in DOCX file")
    return text
