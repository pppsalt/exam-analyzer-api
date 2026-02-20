"""
Reference Parser — generates JSON files from reference PDFs.
Run locally: python parse_references.py /path/to/pdf/folder
Produces JSON files in reference_data/ folder.
"""
import re
import os
import sys
import json

try:
    import pdfplumber
except ImportError:
    print("Install pdfplumber: pip install pdfplumber")
    sys.exit(1)

FILE_MAP = {
    "Sub-Topic-Physics-JEE":    {"exam": "JEE",  "subject": "Physics"},
    "Sub-topic-Physics-NEET":   {"exam": "NEET", "subject": "Physics"},
    "Subtopic-Maths-JEE":      {"exam": "JEE",  "subject": "Mathematics"},
    "Subtopic-Chemistry-JEE":  {"exam": "JEE",  "subject": "Chemistry"},
    "Subtopic-Chemistry-NEET": {"exam": "NEET", "subject": "Chemistry"},
    "Subtopic-Bio-NEET":       {"exam": "NEET", "subject": "Biology"},
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "reference_data")


def identify_file(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    for key, meta in FILE_MAP.items():
        if key.lower() in base.lower():
            return meta
    return None


def parse_pdf(pdf_path):
    meta = identify_file(pdf_path)
    if not meta:
        print(f"  ⚠ Cannot identify: {pdf_path}")
        return None, []

    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=2, y_tolerance=2)
            if t:
                text += t + "\n"

    entries = []
    current_unit_num = ""
    current_unit_name = ""

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        unit_match = re.match(r'(?:Unit\s+)?(\d+)\s*[\.::\-]\s*(.+)', line, re.IGNORECASE)
        if unit_match and "." not in unit_match.group(1):
            cand_num = unit_match.group(1)
            cand_name = unit_match.group(2).strip()
            if len(cand_name) > 3 and not re.match(r'^\d', cand_name):
                current_unit_num = cand_num
                current_unit_name = cand_name
                continue

        sub_match = re.match(r'(\d+\.\d+)\.?\s+(.+)', line)
        if sub_match:
            sub_num = sub_match.group(1)
            sub_name = sub_match.group(2).strip()
            inferred_unit = sub_num.split(".")[0]
            if not current_unit_num or current_unit_num != inferred_unit:
                current_unit_num = inferred_unit
            entries.append({
                "unit_number": current_unit_num,
                "unit_name": current_unit_name or f"Unit {current_unit_num}",
                "subtopic_number": sub_num,
                "subtopic_name": sub_name,
            })

    return meta, entries


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_references.py <pdf_folder_or_file>")
        print("Generates JSON files in reference_data/ folder")
        sys.exit(1)

    path = sys.argv[1]
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = []
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(".pdf")]
    else:
        files = [path]

    print(f"Processing {len(files)} file(s)...\n")

    for fpath in files:
        print(f"📄 {os.path.basename(fpath)}")
        meta, entries = parse_pdf(fpath)
        if not meta:
            continue

        outfile = f"{meta['exam']}_{meta['subject']}.json"
        outpath = os.path.join(OUTPUT_DIR, outfile)

        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

        print(f"   ✅ {len(entries)} subtopics → {outfile}\n")

    print("Done! JSON files saved to reference_data/")
    print("Commit and push to redeploy on Render.")


if __name__ == "__main__":
    main()
