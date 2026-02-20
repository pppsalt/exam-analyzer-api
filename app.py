"""
AI Analysis Engine
Sends extracted questions to selected AI model via OpenRouter.
Returns structured classification per question.
"""
import os
import json
import logging
import time
import requests



logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert exam paper analyzer for Indian competitive exams (JEE Main, JEE Advanced, NEET-UG).

TASK: Analyze the following extracted exam paper text. For each question, provide structured classification.

RULES FOR question_text:
- Preserve ALL Unicode characters exactly as they appear
- Use Unicode subscripts: H₂O, CO₃²⁻, NH₄⁺, x₁, x₂, aₙ
- Use Unicode superscripts: x², e⁻⁴, A²⁰²⁵, 10⁻³, m³
- Use Greek letters: α, β, γ, θ, λ, Δ, Σ, π, ∞, ε, μ, ω, φ
- Use math symbols: ∫, ∑, √, ≥, ≤, ≠, →, ⇌, ∈, ℝ, ℂ, ∂, ∇
- Use vector notation: a⃗, î, ĵ, k̂
- Write fractions inline: dy/dx, x²/a², sin²x/cos²x
- For matrices, write as: [[a,b],[c,d]] format

RULES FOR classification:
- subject: "Physics", "Chemistry", "Mathematics", or "Biology"
- topic: The broad unit/chapter name
- subtopic_name: Be as SPECIFIC as possible — this will be matched to a reference database. Use exact terminology like "Evaluation of definite integrals" not just "Integration"
- concept_tested: One clear sentence describing the exact concept or principle
- difficulty: 
  * "Easy" = direct formula application, single concept, 1-2 steps
  * "Moderate" = multi-step OR combines 2 concepts
  * "Difficult" = multi-concept integration, non-standard approach, lengthy derivation
- has_diagram: true if the question references a figure, diagram, circuit, graph, or structure
- diagram_description: if has_diagram is true, describe what the diagram shows

RESPOND IN JSON FORMAT ONLY — no markdown, no explanation:
{
  "exam_type": "JEE",
  "questions": [
    {
      "sno": 1,
      "question_text": "...",
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


def build_messages(extracted_text: str, exam_type: str = None, subject: str = None) -> list:
    """Build the message payload for the AI model."""
    user_content = f"Analyze this exam paper and classify each question:\n\n{extracted_text}"

    if exam_type and exam_type != "UNKNOWN":
        user_content += f"\n\nNote: This is a {exam_type} pattern paper."
    if subject and subject != "UNKNOWN":
        user_content += f"\nSubject: {subject}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def call_openrouter(model_id: str, messages: list, retry: int = 0) -> dict:
    """Call OpenRouter API with the selected model."""
    if not os.environ.get("OPENROUTER_API_KEY", ""):
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    headers = {
        "Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", ""),
        "Content-Type": "application/json",
        "HTTP-Referer": "https://exam-analyzer.local",
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 8000,
    }

    try:
        logger.info(f"Calling OpenRouter with model: {model_id} (attempt {retry + 1})")
        start = time.time()

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        elapsed = time.time() - start
        logger.info(f"OpenRouter responded in {elapsed:.1f}s — status {response.status_code}")

        if response.status_code != 200:
            error = response.text
            logger.error(f"OpenRouter error: {error}")
            if retry < 2:
                time.sleep(2)
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

    return parsed


def chunk_text_for_token_limit(questions: list, max_chars: int = 15000) -> list:
    """
    Split questions into chunks that fit within token limits.
    Returns list of chunks, each is a list of questions.
    """
    chunks = []
    current_chunk = []
    current_size = 0

    for q in questions:
        q_size = len(q["text"]) + len(q["label"]) + 50
        if current_size + q_size > max_chars and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(q)
        current_size += q_size

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def format_questions_for_prompt(questions: list) -> str:
    """Format extracted questions into a clean text block for the AI prompt."""
    lines = []
    for q in questions:
        lines.append(f"Q.{q['number']}: {q['text']}")
        if q.get("has_diagram"):
            lines.append(f"  [This question contains a diagram/figure]")
        lines.append("")
    return "\n".join(lines)


def analyze(questions: list, model_id: str, exam_type: str = None, subject: str = None) -> dict:
    """
    Main entry point: analyze a list of questions with the selected AI model.
    Handles chunking for large papers.
    Returns combined classification results.
    """
    chunks = chunk_text_for_token_limit(questions)
    logger.info(f"Split {len(questions)} questions into {len(chunks)} chunk(s)")

    all_results = []
    total_time = 0
    detected_exam = exam_type

    for i, chunk in enumerate(chunks):
        prompt_text = format_questions_for_prompt(chunk)
        messages = build_messages(prompt_text, exam_type, subject)

        response = call_openrouter(model_id, messages)
        total_time += response["elapsed"]

        parsed = parse_ai_response(response["content"])

        if not detected_exam or detected_exam == "UNKNOWN":
            detected_exam = parsed.get("exam_type", "UNKNOWN")

        all_results.extend(parsed["questions"])

    return {
        "exam_type": detected_exam,
        "questions": all_results,
        "model_used": model_id,
        "processing_time": total_time,
        "chunks_processed": len(chunks),
    }
