"""
PDF Extractor — Vision-based approach
Converts PDF pages to images for AI vision models.
Also extracts basic text for metadata detection (exam type, subject).
"""
import os
import base64
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Image settings
PAGE_DPI = 200  # Good balance of quality vs size for math formulas
MAX_PAGES = 30  # Safety limit


def pdf_pages_to_images(pdf_path: str, dpi: int = PAGE_DPI) -> list:
    """
    Convert each PDF page to a PNG image, return as base64 strings.
    """
    doc = fitz.open(pdf_path)
    page_images = []

    page_count = min(len(doc), MAX_PAGES)
    if len(doc) > MAX_PAGES:
        logger.warning(f"PDF has {len(doc)} pages, processing first {MAX_PAGES} only")

    for i in range(page_count):
        page = doc[i]
        # Render page to image at specified DPI
        zoom = dpi / 72  # 72 is default PDF DPI
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        page_images.append(b64)
        logger.info(f"Page {i+1}: rendered {pix.width}x{pix.height}px ({len(img_bytes)//1024}KB)")

    doc.close()
    return page_images


def extract_text_for_metadata(pdf_path: str) -> str:
    """
    Extract raw text from PDF — used ONLY for detecting exam type and subject.
    Not used for question analysis (vision handles that).
    """
    doc = fitz.open(pdf_path)
    text = ""
    # Only need first 3 pages for metadata detection
    for i in range(min(3, len(doc))):
        text += doc[i].get_text() + "\n"
    doc.close()
    return text


def detect_exam_type(text: str) -> str:
    """Detect JEE or NEET from paper content."""
    text_lower = text.lower()

    jee_signals = ["jee main", "jee advanced", "jee mains", "iit jee", "jee (main)"]
    neet_signals = ["neet", "neet-ug", "neet ug", "national eligibility"]

    jee_score = sum(1 for s in jee_signals if s in text_lower)
    neet_score = sum(1 for s in neet_signals if s in text_lower)

    if jee_score > neet_score:
        return "JEE"
    elif neet_score > jee_score:
        return "NEET"

    # Fallback: biology = NEET
    if "biology" in text_lower or "botany" in text_lower or "zoology" in text_lower:
        return "NEET"

    return "UNKNOWN"


def detect_subject(text: str) -> str:
    """Detect subject from paper header text."""
    text_lower = text.lower()

    if any(w in text_lower for w in ["mathematics", "maths", "math", "sr-mathematics"]):
        return "Mathematics"
    if any(w in text_lower for w in ["physics", "sr-physics"]):
        return "Physics"
    if any(w in text_lower for w in ["chemistry", "sr-chemistry"]):
        return "Chemistry"
    if any(w in text_lower for w in ["biology", "botany", "zoology", "sr-biology"]):
        return "Biology"

    return "UNKNOWN"


def process_pdf(pdf_path: str, temp_dir: str) -> dict:
    """
    Main entry point.
    Converts PDF to page images + detects metadata.
    Returns everything needed for AI vision analysis.
    """
    logger.info(f"Processing PDF: {pdf_path}")

    # 1. Convert pages to images for AI vision
    page_images = pdf_pages_to_images(pdf_path)
    logger.info(f"Converted {len(page_images)} pages to images")

    # 2. Extract text only for metadata detection
    metadata_text = extract_text_for_metadata(pdf_path)
    exam_type = detect_exam_type(metadata_text)
    subject = detect_subject(metadata_text)
    logger.info(f"Detected exam={exam_type}, subject={subject}")

    return {
        "pdf_path": pdf_path,
        "page_count": len(page_images),
        "page_images": page_images,
        "exam_type": exam_type,
        "subject": subject,
    }
