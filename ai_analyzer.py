"""
AI Analysis Engine — Vision-based
Sends PDF page images directly to a vision-capable AI model via OpenRouter.
The AI reads the actual paper (math, diagrams, everything) and returns structured classification.
"""
import os
import json
import logging
import time
import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert exam paper analyzer for Indian competitive exams (JEE Main, JEE Advanced, NEET-UG).

TASK: You are given images of an exam paper. Look at each page carefully. Identify and classify every question you can see.

CRITICAL RULES FOR question_text:
- READ the actual mathematical expressions, formulas, and symbols from the images
- Write question text using Unicode characters:
  • Subscripts: H₂O, CO₃²⁻, NH₄⁺, x₁, x₂, aₙ
  • Superscripts: x², e⁻⁴, A²⁰²⁵, 10⁻³, m³
  • Greek letters: α, β, γ, θ, λ, Δ, Σ, π, ∞, ε, μ, ω, φ
  • Math symbols: ∫, ∑, √, ≥, ≤, ≠, →, ⇌, ∈, ℝ, ℂ, ∂, ∇
  • Vectors: a⃗, î, ĵ, k̂
  • Fractions inline: dy/dx, x²/a², sin²x/cos²x
  • Matrices: [[a,b],[c,d]] format
- Include the answer options (A), (B), (C), (D) in the question_text if visible
- Do NOT include headers, footers, page numbers, watermarks, or venue information in question_text
- If a question has a diagram/figure/graph/circuit, set has_diagram to true and describe it

RULES FOR classification:
- subject: "Physics", "Chemistry", "Mathematics", or "Biology"
- topic: The broad unit/chapter name (e.g., "Three Dimensional Geometry", "Thermodynamics")
- subtopic_name: Be SPECIFIC — use exact terminology like "Shortest distance between two skew lines" not just "3D Geometry"
- concept_tested: One clear sentence describing the exact concept or principle being tested
- difficulty:
  * "Easy" = direct formula application, single concept, 1-2 steps
  * "Moderate" = multi-step OR combines 2 concepts
  * "Difficult" = multi-concept integration, non-standard approach, lengthy derivation
- has_diagram: true if the question has any figure, diagram, circuit, graph, or chemical structure
- diagram_description: if has_diagram is true, describe what the diagram shows

RESPOND IN JSON FORMAT ONLY — no markdown, no explanation:
{
  "exam_type": "JEE",
  "questions": [
    {
      "sno": 1,
      "question_text": "Full question with math symbols and options...",
      "subject": "Mathematics",
      "topic": "...",
      "subtopic_name": "...",
      "concept_tested": "...",
      "difficulty": "Moderate",
      "has_diagram": false,
      "diagram_description": null
    }
  ]
}"""


def build_vision_messages(page_images: list, exam_type: str = None, subject: str = None) -> list:
    """
    Build multimodal message payload with page images for vision AI.
    Each page image is sent as a base64 image_url.
    """
    # Build the user message content: images first, then text instruction
    content = []

    # Add each page as an image
    for i, b64 in enumerate(page_images):
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}"
            }
        })

    # Add text instruction
    instruction = "Analyze this exam paper. Look at every page image above. Identify and classify each question."
    if exam_type and exam_type != "UNKNOWN":
        instruction += f"\n\nNote: This is a {exam_type} pattern paper."
    if subject and subject != "UNKNOWN":
        instruction += f"\nSubject: {subject}"

    content.append({"type": "text", "text": instruction})

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def call_openrouter(model_id: str, messages: list, retry: int = 0) -> dict:
    """Call OpenRouter API with the selected model (vision-capable)."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://exam-analyzer.local",
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 16000,  # Higher limit for vision responses with full question text
    }

    try:
        logger.info(f"Calling OpenRouter with model: {model_id} (attempt {retry + 1})")
        start = time.time()

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180,  # Vision models can take longer
        )
        elapsed = time.time() - start
        logger.info(f"OpenRouter responded in {elapsed:.1f}s — status {response.status_code}")

        if response.status_code != 200:
            error = response.text
            logger.error(f"OpenRouter error: {error}")
            if retry < 2:
                time.sleep(3)
                return call_openrouter(model_id, messages, retry + 1)
            raise RuntimeError(f"OpenRouter API error: {response.status_code} — {error}")

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        return {"content": content, "elapsed": elapsed, "model": model_id}

    except requests.exceptions.Timeout:
        logger.warning(f"Timeout calling {model_id}")
        if retry < 2:
            return call_openrouter(model_id, messages, retry + 1)
        raise


def parse_ai_response(raw_content: str) -> dict:
    """Parse the AI's JSON response, handling common formatting issues."""
    content = raw_content.strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    content = content.strip()
    if content.startswith("json"):
        content = content[4:].strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        logger.error(f"Raw content (first 500 chars): {content[:500]}")
        # Try to extract JSON object from the response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(content[start:end])
            except json.JSONDecodeError:
                raise ValueError(f"Could not parse AI response as JSON: {e}")
        else:
            raise ValueError(f"No JSON object found in AI response: {e}")

    # Validate structure
    if "questions" not in parsed:
        raise ValueError("AI response missing 'questions' key")

    for q in parsed["questions"]:
        required = ["sno", "question_text", "subject", "topic", "subtopic_name", "difficulty"]
        missing = [k for k in required if k not in q]
        if missing:
            logger.warning(f"Question {q.get('sno', '?')} missing fields: {missing}")
            for k in missing:
                q[k] = "Unknown" if k != "sno" else 0

        # Normalize difficulty
        diff = q.get("difficulty", "").strip().capitalize()
        if diff not in ("Easy", "Moderate", "Difficult"):
            q["difficulty"] = "Moderate"
        else:
            q["difficulty"] = diff

        # Add question_label if not present
        if "question_label" not in q:
            q["question_label"] = f"Q.{q.get('sno', 0)}"

    return parsed


def chunk_pages(page_images: list, max_pages_per_chunk: int = 8) -> list:
    """
    Split page images into chunks for very large papers.
    Most vision models handle 8-10 images well per request.
    """
    chunks = []
    for i in range(0, len(page_images), max_pages_per_chunk):
        chunks.append(page_images[i:i + max_pages_per_chunk])
    return chunks


def analyze(page_images: list, model_id: str, exam_type: str = None, subject: str = None) -> dict:
    """
    Main entry point: analyze PDF page images with a vision-capable AI model.
    Handles chunking for large papers (>8 pages).
    Returns combined classification results.
    """
    chunks = chunk_pages(page_images)
    logger.info(f"Processing {len(page_images)} pages in {len(chunks)} chunk(s)")

    all_results = []
    total_time = 0
    detected_exam = exam_type

    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} pages)")
        messages = build_vision_messages(chunk, exam_type, subject)

        response = call_openrouter(model_id, messages)
        total_time += response["elapsed"]

        parsed = parse_ai_response(response["content"])

        if not detected_exam or detected_exam == "UNKNOWN":
            detected_exam = parsed.get("exam_type", "UNKNOWN")

        all_results.extend(parsed["questions"])

    # Re-number if we had multiple chunks (avoid duplicate sno)
    if len(chunks) > 1:
        for idx, q in enumerate(all_results):
            q["sno"] = idx + 1
            q["question_label"] = f"Q.{idx + 1}"

    return {
        "exam_type": detected_exam,
        "questions": all_results,
        "model_used": model_id,
        "processing_time": total_time,
        "chunks_processed": len(chunks),
    }
