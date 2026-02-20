"""
PDF Text & Image Extraction Module
Extracts question text (Unicode preserved) and embedded diagram images from exam PDFs.
"""
import re
import os
import logging
from pathlib import Path

import pdfplumber
from PIL import Image
import io

logger = logging.getLogger(__name__)


def extract_text(pdf_path: str) -> dict:
    """
    Extract all text from PDF preserving Unicode.
    Returns dict with full_text, pages, and detected metadata.
    """
    result = {"full_text": "", "pages": [], "page_count": 0}

    with pdfplumber.open(pdf_path) as pdf:
        result["page_count"] = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            result["pages"].append({"page_num": i + 1, "text": text})
            result["full_text"] += text + "\n"

    return result


def extract_embedded_images(pdf_path: str, output_dir: str) -> list:
    """
    Extract embedded image objects from PDF.
    These are diagrams/figures the author inserted — NOT page renders.
    Returns list of {path, page_num, bbox, width, height}.
    """
    os.makedirs(output_dir, exist_ok=True)
    images = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            page_images = page.images
            for img_idx, img in enumerate(page_images):
                try:
                    # Get image bbox on page
                    x0, y0, x1, y1 = img["x0"], img["top"], img["x1"], img["bottom"]
                    w = x1 - x0
                    h = y1 - y0

                    # Skip tiny images (logos, bullets, decorations)
                    if w < 50 or h < 50:
                        continue

                    # Crop the image area from the page
                    cropped = page.within_bbox((x0, y0, x1, y1))
                    pil_img = cropped.to_image(resolution=200).original

                    fname = f"diagram_p{page_num + 1}_i{img_idx + 1}.png"
                    fpath = os.path.join(output_dir, fname)
                    pil_img.save(fpath)

                    images.append({
                        "path": fpath,
                        "filename": fname,
                        "page_num": page_num + 1,
                        "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                        "width": w,
                        "height": h,
                    })
                except Exception as e:
                    logger.warning(f"Failed to extract image {img_idx} from page {page_num + 1}: {e}")

    return images


def detect_questions(full_text: str) -> list:
    """
    Split extracted text into individual questions.
    Handles patterns: Q.1, Q1, Q 1, 1., 1), (1), Question 1, etc.
    Returns list of {number, label, text, start_pos, end_pos}.
    """
    # Common question number patterns in Indian exam papers
    patterns = [
        r'(?:^|\n)\s*(Q\s*[\.\:]\s*(\d+))',          # Q.1, Q:1, Q .1
        r'(?:^|\n)\s*(Q(\d+))\s',                      # Q1 Q2
        r'(?:^|\n)\s*(Question\s+(\d+))',               # Question 1
        r'(?:^|\n)\s*(?<!\S)((\d+)\s*[\.\)]\s)',        # 1. or 1)
    ]

    matches = []
    for pattern in patterns:
        for m in re.finditer(pattern, full_text, re.IGNORECASE | re.MULTILINE):
            q_num = int(m.group(2))
            matches.append({
                "number": q_num,
                "label": m.group(1).strip().rstrip(".):"),
                "start": m.start(),
                "match_end": m.end(),
            })

    if not matches:
        logger.warning("No question boundaries detected. Returning full text as single block.")
        return [{"number": 0, "label": "Full Paper", "text": full_text.strip()}]

    # Deduplicate: keep earliest match per question number
    seen = {}
    for m in sorted(matches, key=lambda x: x["start"]):
        if m["number"] not in seen:
            seen[m["number"]] = m
    matches = sorted(seen.values(), key=lambda x: x["start"])

    # Extract text between question boundaries
    questions = []
    for i, m in enumerate(matches):
        start = m["match_end"]
        end = matches[i + 1]["start"] if i + 1 < len(matches) else len(full_text)
        text = full_text[start:end].strip()

        # Clean up: remove answer options markers if at the very end
        # but keep them as part of the question
        questions.append({
            "number": m["number"],
            "label": f"Q.{m['number']}",
            "text": text,
        })

    return questions


def map_images_to_questions(images: list, questions: list, page_texts: list) -> dict:
    """
    Map extracted images to the nearest question based on page position.
    Returns dict: question_number -> [list of image paths]
    """
    mapping = {}

    if not images or not questions:
        return mapping

    # For each image, find which question it belongs to by checking
    # which question text appears on the same page near the image's y-position
    for img in images:
        page_num = img["page_num"]
        img_y = img["bbox"]["y0"]

        # Simple heuristic: assign to question whose text appears
        # closest above this image on the same page
        best_q = None
        for q in questions:
            # Check if question label appears on this page
            if page_num <= len(page_texts):
                page_text = page_texts[page_num - 1]["text"]
                if f"Q.{q['number']}" in page_text or f"Q{q['number']}" in page_text:
                    best_q = q["number"]

        if best_q is not None:
            mapping.setdefault(best_q, []).append(img["path"])

    return mapping


def detect_exam_type(full_text: str) -> str:
    """Detect JEE or NEET from paper content."""
    text_lower = full_text.lower()

    jee_signals = ["jee main", "jee advanced", "jee mains", "iit jee", "jee (main)"]
    neet_signals = ["neet", "neet-ug", "neet ug", "national eligibility"]

    jee_score = sum(1 for s in jee_signals if s in text_lower)
    neet_score = sum(1 for s in neet_signals if s in text_lower)

    if jee_score > neet_score:
        return "JEE"
    elif neet_score > jee_score:
        return "NEET"

    # Fallback: check for subject indicators
    if "biology" in text_lower or "botany" in text_lower or "zoology" in text_lower:
        return "NEET"

    return "UNKNOWN"


def detect_subject(text: str) -> str:
    """Detect subject from question text or paper header."""
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
    Main entry point: extract everything from a PDF.
    Returns complete extraction result.
    """
    logger.info(f"Processing PDF: {pdf_path}")

    # 1. Extract text
    text_result = extract_text(pdf_path)
    logger.info(f"Extracted {text_result['page_count']} pages of text")

    # 2. Extract embedded images
    img_dir = os.path.join(temp_dir, "images")
    images = extract_embedded_images(pdf_path, img_dir)
    logger.info(f"Extracted {len(images)} embedded images")

    # 3. Detect questions
    questions = detect_questions(text_result["full_text"])
    logger.info(f"Detected {len(questions)} questions")

    # 4. Map images to questions
    image_map = map_images_to_questions(images, questions, text_result["pages"])

    # 5. Attach images to questions
    for q in questions:
        q["diagram_paths"] = image_map.get(q["number"], [])
        q["has_diagram"] = len(q["diagram_paths"]) > 0

    # 6. Detect exam type and subject
    exam_type = detect_exam_type(text_result["full_text"])
    subject = detect_subject(text_result["full_text"])

    return {
        "pdf_path": pdf_path,
        "page_count": text_result["page_count"],
        "exam_type": exam_type,
        "subject": subject,
        "questions": questions,
        "images_extracted": len(images),
        "full_text": text_result["full_text"],
    }
