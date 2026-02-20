"""
Subtopic Matcher — Render version
Loads reference data from JSON files (no MySQL).
"""
import os
import json
import logging
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

FUZZY_MATCH_THRESHOLD = 60
REF_DIR = os.path.join(os.path.dirname(__file__), "reference_data")
_cache = {}


def load_reference(exam_type: str, subject: str) -> list:
    key = f"{exam_type}_{subject}"
    if key in _cache:
        return _cache[key]

    filename = f"{exam_type}_{subject}.json"
    filepath = os.path.join(REF_DIR, filename)

    if not os.path.exists(filepath):
        logger.warning(f"No reference file: {filepath}")
        _cache[key] = []
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        rows = json.load(f)

    _cache[key] = rows
    logger.info(f"Loaded {len(rows)} reference subtopics for {exam_type}/{subject}")
    return rows


def match_subtopic(ai_subtopic: str, ai_topic: str, exam_type: str, subject: str) -> dict:
    refs = load_reference(exam_type, subject)
    if not refs:
        return {"subtopic_number": "N/A", "matched_name": None, "confidence": 0, "unit_name": None}

    candidates = []
    for r in refs:
        candidates.append({"ref": r, "text": r["subtopic_name"]})
        candidates.append({"ref": r, "text": f"{r['unit_name']}: {r['subtopic_name']}"})

    queries = [ai_subtopic, f"{ai_topic}: {ai_subtopic}", f"{ai_topic} {ai_subtopic}"]
    best_match, best_score = None, 0

    for query in queries:
        for cand in candidates:
            score = fuzz.token_sort_ratio(query.lower(), cand["text"].lower())
            if score > best_score:
                best_score = score
                best_match = cand["ref"]

    if best_match:
        return {
            "subtopic_number": best_match["subtopic_number"],
            "matched_name": best_match["subtopic_name"],
            "unit_name": best_match["unit_name"],
            "confidence": best_score,
        }
    return {"subtopic_number": "N/A", "matched_name": None, "confidence": 0, "unit_name": None}


def match_all(questions: list, exam_type: str) -> list:
    for q in questions:
        subject = q.get("subject", "Mathematics")
        result = match_subtopic(q.get("subtopic_name", ""), q.get("topic", ""), exam_type, subject)
        if result["confidence"] < FUZZY_MATCH_THRESHOLD:
            alt = "NEET" if exam_type == "JEE" else "JEE"
            alt_result = match_subtopic(q.get("subtopic_name", ""), q.get("topic", ""), alt, subject)
            if alt_result["confidence"] > result["confidence"]:
                result = alt_result
        q["subtopic_number"] = result["subtopic_number"]
        q["match_confidence"] = result["confidence"]
        q["matched_subtopic_name"] = result["matched_name"]
        q["matched_unit_name"] = result["unit_name"]
    return questions


def get_stats() -> list:
    stats = []
    if not os.path.exists(REF_DIR):
        return stats
    for f in os.listdir(REF_DIR):
        if f.endswith(".json"):
            parts = f.replace(".json", "").split("_", 1)
            if len(parts) == 2:
                filepath = os.path.join(REF_DIR, f)
                with open(filepath, "r") as fh:
                    data = json.load(fh)
                stats.append({"exam_type": parts[0], "subject": parts[1], "count": len(data)})
    return stats


def clear_cache():
    _cache.clear()
