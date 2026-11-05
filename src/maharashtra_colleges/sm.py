"""
Maharashtra CET Cell - CAP Round Seat Matrix PDF → CSV
Extracts: Code, College, Branch (MBBS/BDS), Type (Government/Private)

Usage:
    pip install pdfplumber pandas
    python extract_seat_matrix.py

Place CAP_Round1_SM.pdf in the same folder or update PDF_PATH below.
"""

import pdfplumber
import pandas as pd
import re
import sys
import os

# ── CONFIG ────────────────────────────────────────────────────────────────────
PDF_PATH   = "../../docs/maharashtra_colleges/stray_round_sm.pdf"
OUTPUT_CSV = "../../data/maharashtra_colleges/stray_round_colleges.csv"
# ─────────────────────────────────────────────────────────────────────────────

COLUMNS = ["Code", "College", "Branch", "Type"]

# Section heading patterns → (Type, Branch)
SECTION_MAP = {
    re.compile(r"GOVT\.\s*COLLEGES\s*:\s*MBBS", re.I): ("Government", "MBBS"),
    re.compile(r"PVT\.\s*COLLEGES\s*:\s*MBBS",  re.I): ("Private",    "MBBS"),
    re.compile(r"GOVT\.\s*COLLEGES\s*:\s*BDS",  re.I): ("Government", "BDS"),
    re.compile(r"PVT\.\s*COLLEGES\s*:\s*BDS",   re.I): ("Private",    "BDS"),
}

# College entry: Sr (1-3 digits)  Code (4-6 digits)  Name...  Y/N  number
ENTRY_RE = re.compile(
    r"^\s*(\d{1,3})\s+"   # Sr No
    r"(\d{4,6})\s+"       # College Code
    r"(.+?)\s+"           # College Name (non-greedy, cleaned later)
    r"[NY]\s+"            # Govt/Aided flag
    r"\d"                 # Capacity starts with a digit — confirms data row
)

# Lines that definitely are NOT part of a college name
NOISE_RE = re.compile(
    r"^(GEN\.|WOM\.|ORP|PWD|ORPC|GEN HA|WOMEN|Legends|Note:|---|\*\s*\*|=+|"
    r"Sr\s+Code|Aided\s+Cap|Req\.\s+Of|Admissions|Provisional|Status\s*:)",
    re.I
)

# Junk that can bleed into a name from continuation lines (WOM. data, pipes etc.)
NAME_JUNK_RE = re.compile(
    r"\s*(WOM\.|GEN\.|N\s+\d|\|\s*\d|={3,}|\*\s*\*|"
    r"\d+\s*\|\s*\d+|[|]{1}\s*\d).*$"
)


def clean_name(name: str) -> str:
    """Remove any trailing seat-matrix data that bled into the name."""
    name = NAME_JUNK_RE.sub("", name)
    # Also strip trailing  (MUSLIM)  tags and similar
    name = re.sub(r"\s*\(MUSLIM\)\s*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_colleges(pdf_path: str) -> list:
    current_type, current_branch = None, None
    results = []
    seen_codes = set()

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"[INFO] Total pages: {total}")

        for pg_idx, page in enumerate(pdf.pages):
            text = page.extract_text(layout=False) or ""
            lines = text.splitlines()

            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # 1 — Detect section heading
                for pat, (t, b) in SECTION_MAP.items():
                    if pat.search(line):
                        current_type, current_branch = t, b
                        break

                # 2 — Detect college entry row
                if current_type and current_branch:
                    m = ENTRY_RE.match(line)
                    if m:
                        code = m.group(2)
                        name = m.group(3).strip()

                        # Collect continuation lines (multi-line college names)
                        j = i + 1
                        while j < len(lines):
                            nxt = lines[j].strip()
                            if (not nxt
                                    or NOISE_RE.match(nxt)
                                    or ENTRY_RE.match(nxt)
                                    or any(p.search(nxt) for p in SECTION_MAP)):
                                break
                            name = name + " " + nxt
                            j += 1

                        name = clean_name(name)

                        if code not in seen_codes and name:
                            seen_codes.add(code)
                            results.append([code, name, current_branch, current_type])

                i += 1

    return results


def main():
    if not os.path.exists(PDF_PATH):
        print(f"[ERROR] PDF not found: '{PDF_PATH}'")
        print(f"        Place '{PDF_PATH}' in the same folder as this script.")
        sys.exit(1)

    print(f"[INFO] Extracting from: {PDF_PATH}\n")
    rows = extract_colleges(PDF_PATH)

    if not rows:
        print("[ERROR] No records extracted. Check PDF structure.")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df.sort_values(["Branch", "Type", "Code"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"[SUCCESS] {len(df)} colleges saved to '{OUTPUT_CSV}'\n")
    print("Breakdown:")
    print(df.groupby(["Branch", "Type"]).size().to_string())
    print(f"\nAll colleges:")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()