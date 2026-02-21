"""
Exam Analyzer — Render Web Service
Accepts PDF upload → Converts pages to images → AI Vision analyzes → Matches Subtopics → Returns DOCX/XLSX
No MySQL dependency — reference data loaded from JSON files.
"""
import os
import time
import uuid
import json
import logging
import tempfile
import traceback
from datetime import datetime

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

import pdf_extractor
import ai_analyzer
import subtopic_matcher
import docx_generator
import xlsx_generator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("exam-analyzer")

app = Flask(__name__)
CORS(app)  # Allow WordPress to call from different domain

TEMP_DIR = tempfile.mkdtemp(prefix="exam_")
OUTPUT_DIR = os.path.join(TEMP_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.route("/health", methods=["GET"])
def health():
    ref_stats = subtopic_matcher.get_stats()
    return jsonify({
        "status": "ok",
        "version": "2.0-vision",
        "timestamp": datetime.now().isoformat(),
        "reference_data": ref_stats,
    })


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Main endpoint. Accepts multipart form with:
    - pdf_file: the PDF file
    - model_id: OpenRouter model ID (must be vision-capable)
    - exam_type: (optional) JEE or NEET
    - subject: (optional) Physics/Chemistry/Mathematics/Biology
    Returns JSON with analysis results + download URLs for DOCX/XLSX.
    """
    pdf_file = request.files.get("pdf_file")
    model_id = request.form.get("model_id", "google/gemini-2.5-flash")
    forced_exam = request.form.get("exam_type", "")
    forced_subject = request.form.get("subject", "")
    upload_id = request.form.get("upload_id", "")

    if not pdf_file:
        return jsonify({"status": "failed", "error": "No PDF file uploaded"}), 400

    if not pdf_file.filename.lower().endswith(".pdf"):
        return jsonify({"status": "failed", "error": "Only PDF files allowed"}), 400

    start_time = time.time()
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # Save uploaded PDF
    pdf_path = os.path.join(job_dir, pdf_file.filename)
    pdf_file.save(pdf_path)

    try:
        # Step 1: Convert PDF pages to images + detect metadata
        logger.info(f"[{job_id}] Step 1: Converting PDF to images")
        extraction = pdf_extractor.process_pdf(pdf_path, job_dir)
        page_images = extraction["page_images"]
        exam_type = forced_exam or extraction["exam_type"]
        subject = forced_subject or extraction["subject"]

        if not page_images:
            raise ValueError("No pages found in PDF")

        logger.info(f"[{job_id}] Got {len(page_images)} page images, exam={exam_type}, subject={subject}")

        # Step 2: AI Vision Analysis — send page images to AI
        logger.info(f"[{job_id}] Step 2: AI Vision Analysis with {model_id}")
        ai_result = ai_analyzer.analyze(page_images, model_id, exam_type, subject)
        ai_questions = ai_result["questions"]

        if not ai_questions:
            raise ValueError("AI did not detect any questions in the paper")

        logger.info(f"[{job_id}] AI detected {len(ai_questions)} questions")

        # Update exam_type from AI if we didn't have it
        if not exam_type or exam_type == "UNKNOWN":
            exam_type = ai_result.get("exam_type", "UNKNOWN")

        # Step 3: Subtopic Matching
        logger.info(f"[{job_id}] Step 3: Subtopic matching")
        if exam_type and exam_type != "UNKNOWN":
            ai_questions = subtopic_matcher.match_all(ai_questions, exam_type)
        else:
            for q in ai_questions:
                q["subtopic_number"] = "N/A"
                q["match_confidence"] = 0

        # Step 4: Generate outputs
        logger.info(f"[{job_id}] Step 4: Generating DOCX/XLSX")
        paper_name = os.path.splitext(pdf_file.filename)[0]
        metadata = {
            "paper_name": paper_name,
            "exam_type": exam_type,
            "subject": subject,
            "model_used": model_id,
        }

        docx_filename = f"{paper_name}_Analysis_{job_id}.docx"
        xlsx_filename = f"{paper_name}_Analysis_{job_id}.xlsx"
        docx_path = os.path.join(OUTPUT_DIR, docx_filename)
        xlsx_path = os.path.join(OUTPUT_DIR, xlsx_filename)

        docx_generator.generate(ai_questions, metadata, docx_path)
        xlsx_generator.generate(ai_questions, metadata, xlsx_path)

        elapsed = time.time() - start_time
        logger.info(f"[{job_id}] Done in {elapsed:.1f}s — {len(ai_questions)} questions")

        return jsonify({
            "status": "completed",
            "job_id": job_id,
            "upload_id": upload_id,
            "questions_count": len(ai_questions),
            "exam_type": exam_type,
            "subject": subject,
            "model_used": model_id,
            "processing_time": round(elapsed, 1),
            "docx_filename": docx_filename,
            "xlsx_filename": xlsx_filename,
            "docx_url": f"/download/{docx_filename}",
            "xlsx_url": f"/download/{xlsx_filename}",
            "questions": ai_questions,  # Full results for WP to store
        })

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[{job_id}] Failed: {e}\n{traceback.format_exc()}")
        return jsonify({
            "status": "failed",
            "error": str(e),
            "processing_time": round(elapsed, 1),
        }), 500


@app.route("/download/<filename>", methods=["GET"])
def download(filename):
    """Download generated DOCX/XLSX file."""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, as_attachment=True)


@app.route("/models", methods=["GET"])
def get_models():
    """Return available AI models (vision-capable only)."""
    models = [
        {"model_id": "google/gemini-2.5-flash", "display_name": "Gemini 2.5 Flash", "provider": "Google", "supports_vision": True},
        {"model_id": "google/gemini-2.5-pro", "display_name": "Gemini 2.5 Pro", "provider": "Google", "supports_vision": True},
        {"model_id": "anthropic/claude-sonnet-4", "display_name": "Claude Sonnet 4", "provider": "Anthropic", "supports_vision": True},
        {"model_id": "anthropic/claude-opus-4", "display_name": "Claude Opus 4", "provider": "Anthropic", "supports_vision": True},
        {"model_id": "openai/gpt-4o", "display_name": "GPT-4o", "provider": "OpenAI", "supports_vision": True},
    ]
    return jsonify({"models": models})


@app.route("/reference-stats", methods=["GET"])
def reference_stats():
    return jsonify({"reference_data": subtopic_matcher.get_stats()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
