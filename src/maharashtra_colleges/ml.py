"""
Maharashtra CET Cell - Merit List PDF → CSV Extractor (Fixed Version)
Correctly extracts all 9 columns including multi-line names, 
Category, NRI, and Specified Reservation.

Usage:
    pip install pdfplumber pandas
    python extract_merit_list.py

Place the Merit List PDF in the same folder or update PDF_PATH below.
"""

import pdfplumber
import pandas as pd
import re
import sys
import os
import time
from multiprocessing import Pool, cpu_count

# ── CONFIG ────────────────────────────────────────────────────────────────────
PDF_PATH    = "../../docs/maharashtra_colleges/merit_list_round3.pdf"   # Change as needed
OUTPUT_CSV  = "../../data/maharashtra_colleges/merit_list_round3.csv"
BATCH_SIZE  = 50
NUM_WORKERS = max(1, cpu_count() - 1)
# ─────────────────────────────────────────────────────────────────────────────

COLUMNS = [
    "Sr.No",
    "NEET All India Rank",
    "NEET ROLL NO",
    "CetCell Online Form No.",
    "Name of The Candidate",
    "Gender",
    "Category",
    "NRI",
    "Specified Reservation",
]

# Lines to skip (headers, footers, disclaimers)
SKIP_RE = re.compile(
    r"Government of Maharashtra|STATE COMMON ENTRANCE|Merit List|NEET-UG|"
    r"Sr\.No\.|India\s+ROLL|^\s*Rank\s*$|EWS as per|NRI - subject|Constitutional|"
    r"Coursewise|Disclaimer|Provisional Merit|admission will|benefit of|"
    r"^\s*$|Page \d+ of|CetCell Online|Name of The|Candidate",
    re.I
)

# Valid category codes
CATEGORY_RE = re.compile(
    r"^(OBC|EWS|SC|ST|SEBC|NT\([A-D]\)|VJ|NTB|NTC|NTD|SBC|DT|NT)-\d+$", re.I
)

GENDER_RE = re.compile(r"^[MF]$")

# Known Specified Reservation token words
SPEC_RES_WORDS = {"HILLY", "AREA", "ORPHAN", "PWD", "DEFENCE", "EX-SERVICEMAN", "DISABLED"}


def parse_tail(tokens):
    """
    From tokens after [Sr, AIR, Roll, Form], extract:
    name, gender, category, nri, specified_reservation

    Scans right-to-left to locate Gender (M/F), then everything
    to the left is the Name, and everything to the right is Category/NRI/SpecRes.
    """
    if not tokens:
        return "", "", "", "", ""

    # Find gender index scanning right to left
    gender_idx = -1
    for i in range(len(tokens) - 1, -1, -1):
        if GENDER_RE.match(tokens[i]):
            rest = tokens[i + 1:]
            valid = all(
                CATEGORY_RE.match(r) or r in SPEC_RES_WORDS or r == "NRI"
                for r in rest
            )
            if valid:
                gender_idx = i
                break

    if gender_idx == -1:
        return " ".join(tokens), "", "", "", ""

    name   = " ".join(tokens[:gender_idx])
    gender = tokens[gender_idx]
    rest   = tokens[gender_idx + 1:]

    category      = ""
    nri           = ""
    spec_res_parts = []

    for token in rest:
        if CATEGORY_RE.match(token) and not category:
            category = token
        elif token == "NRI":
            nri = "Y"
        else:
            spec_res_parts.append(token)

    spec_res = " ".join(spec_res_parts)
    return name, gender, category, nri, spec_res


def is_name_continuation(line):
    """
    A continuation line has blank cols 0-31 and non-empty text after.
    It carries the second part of a candidate's name (split across lines).
    """
    if len(line) < 32:
        return False
    if line[:32].strip():
        return False
    tail = line[32:].strip()
    if not tail or SKIP_RE.search(tail):
        return False
    tokens = tail.split()
    if not tokens:
        return False
    # Must not start with a digit (would be a new record)
    return not tokens[0].isdigit()


def process_page_range(args):
    """Worker: extracts records from a range of pages."""
    pdf_path, start_page, end_page = args
    rows = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for pg_num in range(start_page, min(end_page, total)):
            lines = (pdf.pages[pg_num].extract_text(layout=True) or "").splitlines()

            i = 0
            while i < len(lines):
                line     = lines[i]
                stripped = line.strip()

                if not stripped or SKIP_RE.search(stripped):
                    i += 1
                    continue

                tokens = stripped.split()

                # Valid data row detection:
                # token[0] = Sr.No (int)
                # token[1] = NEET AIR (int)
                # token[2] = NEET Roll No (10 digits)
                # token[3] = CetCell Form No (9 digits)
                # token[4+] = Name, Gender, Category, NRI, SpecRes
                if (len(tokens) >= 5
                        and tokens[0].isdigit()
                        and tokens[1].isdigit()
                        and len(tokens[2]) == 10 and tokens[2].isdigit()
                        and len(tokens[3]) == 9  and tokens[3].isdigit()):

                    sr, air, roll, form = tokens[0], tokens[1], tokens[2], tokens[3]
                    tail_tokens = tokens[4:]

                    # Collect continuation lines (second part of name)
                    # Continuation comes AFTER the main line, so APPEND it to name
                    j = i + 1
                    cont_parts = []
                    while j < len(lines):
                        if is_name_continuation(lines[j]):
                            cont_parts.append(lines[j].strip())
                            j += 1
                        else:
                            break
                    i = j

                    # If there were continuation lines, insert their words into tail_tokens
                    # before the Gender token (i.e., as extra name words)
                    if cont_parts:
                        # Find gender position in current tail_tokens
                        gender_idx = -1
                        for idx in range(len(tail_tokens) - 1, -1, -1):
                            if GENDER_RE.match(tail_tokens[idx]):
                                rest_check = tail_tokens[idx + 1:]
                                if all(CATEGORY_RE.match(r) or r in SPEC_RES_WORDS or r == "NRI"
                                       for r in rest_check):
                                    gender_idx = idx
                                    break

                        cont_tokens = []
                        for part in cont_parts:
                            cont_tokens.extend(part.split())

                        if gender_idx >= 0:
                            # Insert continuation words right before the gender token
                            tail_tokens = (tail_tokens[:gender_idx]
                                           + cont_tokens
                                           + tail_tokens[gender_idx:])
                        else:
                            tail_tokens = tail_tokens + cont_tokens

                    name, gender, category, nri, spec_res = parse_tail(tail_tokens)

                    rows.append([sr, air, roll, form, name, gender, category, nri, spec_res])

                else:
                    i += 1

    return rows


def main():
    if not os.path.exists(PDF_PATH):
        print(f"[ERROR] PDF not found: '{PDF_PATH}'")
        print("        Place the PDF in the same folder or update PDF_PATH.")
        sys.exit(1)

    with pdfplumber.open(PDF_PATH) as pdf:
        total_pages = len(pdf.pages)

    print(f"[INFO] PDF        : {PDF_PATH}")
    print(f"[INFO] Total pages: {total_pages}")
    print(f"[INFO] Workers    : {NUM_WORKERS}")
    print(f"[INFO] Batch size : {BATCH_SIZE} pages\n")

    batches = [
        (PDF_PATH, i, i + BATCH_SIZE)
        for i in range(0, total_pages, BATCH_SIZE)
    ]

    start_time = time.time()
    completed  = 0
    all_rows   = []

    with Pool(processes=NUM_WORKERS) as pool:
        for result in pool.imap_unordered(process_page_range, batches):
            all_rows.extend(result)
            completed += 1
            pct     = (completed / len(batches)) * 100
            elapsed = time.time() - start_time
            eta     = max(0, (elapsed / completed) * len(batches) - elapsed)
            print(
                f"\r[{pct:5.1f}%] {completed}/{len(batches)} batches | "
                f"Rows so far: {len(all_rows):,} | "
                f"Elapsed: {elapsed:.0f}s | ETA: {eta:.0f}s",
                end="", flush=True
            )

    print()

    if not all_rows:
        print("[ERROR] No records extracted.")
        sys.exit(1)

    print(f"[INFO] Building DataFrame...")
    df = pd.DataFrame(all_rows, columns=COLUMNS)

    for col in ["Sr.No", "NEET All India Rank", "NEET ROLL NO"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["Sr.No", "NEET All India Rank"], inplace=True)
    df.sort_values("Sr.No", inplace=True)
    df.reset_index(drop=True, inplace=True)

    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    elapsed = time.time() - start_time
    print(f"\n[SUCCESS] {len(df):,} records saved to '{OUTPUT_CSV}' in {elapsed:.1f}s")
    print(f"\nFirst 15 rows preview:")
    print(df.head(15).to_string(index=False))

    # Quick validation
    print(f"\nCategory distribution (top 10):")
    print(df["Category"].value_counts().head(10).to_string())
    print(f"\nSpecified Reservation distribution:")
    print(df["Specified Reservation"].value_counts().to_string())


if __name__ == "__main__":
    main()