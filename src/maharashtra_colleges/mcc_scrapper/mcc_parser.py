"""
MCC PDF Parser  v2
==================
Key fix: stores raw value text verbatim instead of extracting only the last number.
Correctly handles all value formats:
  "79100"                                       → simple number
  "For UR EWS 40800 and for OBC SC ST 31800"   → category-wise values
  "UR/EWS: 85000, OBC/SC/ST: 70000"            → another format
  "0"                                           → zero
  ""                                            → blank

Usage:
    python mcc_parse_pdfs_v2.py
    python mcc_parse_pdfs_v2.py --pdf-dir pdfs --out mcc_institute_profiles.csv
"""

import re, logging
import pdfplumber, pandas as pd
from pathlib import Path
from tqdm import tqdm


# ── Config ────────────────────────────────────────────────────────────────
PDF_DIR    = Path("pdfs")
CODES_CSV  = "../../../data/maharashtra_colleges/mcc_scrapper/mcc_codes_only.csv"
OUTPUT_CSV = "../../../data/maharashtra_colleges/mcc_scrapper/mcc_institute_profiles.csv"
LOG_FILE   = "parse.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def extract_yesno(line: str) -> str:
    m = re.search(r'\b([YN])\s*$', line.strip())
    return m.group(1) if m else line.split(":")[-1].strip()


def is_new_trigger(line: str) -> bool:
    """Returns True if this line starts a new field — so we should stop collecting."""
    return bool(re.match(
        r'^(Amount to be [Pp]aid|Stipend Paid|The Amount|Hostel facility|'
        r'Annual Fees|Name of |Complete Mailing|State:|Pin Code:|Website|'
        r'Other Info|Miscellaneous|PwBD|Contact|On Date)',
        line.strip(), re.I
    ))


TRIGGER_RE = re.compile(r"Amount to be [Pp]aid at the time of\s*", re.I)
LABEL_END_RE = re.compile(
    r'(INR\)\s*:?\s*$|INR\)\s*:|\$\)\s*:|\(INR\)\s*:|\(INR\)\s*\:\s*$)', re.I
)


def get_value_for_amount_field(lines: list[str], i: int) -> str:
    """
    Extract the complete value for an "Amount to be Paid/paid at the time of" field.
    Value starts after the trigger phrase on line i.
    """
    line = lines[i].strip()
    # Strip trigger phrase
    value = TRIGGER_RE.sub("", line).strip()

    # If nothing after trigger on line i, check next lines for overflowed value
    if not value:
        for j in range(i+1, min(i+6, len(lines))):
            next_s = lines[j].strip()
            if not next_s:
                continue
            if is_new_trigger(next_s):
                break
            if LABEL_END_RE.search(next_s):
                # Value might be after colon on this label-end line
                m = re.search(r":\s*(.+)$", next_s)
                if m:
                    v = m.group(1).strip()
                    if not is_new_trigger(v):
                        value = v
                break
            # If line has digits or Y/N it's a value continuation
            if re.search(r'\d|^[YN]$', next_s):
                value = next_s
                break

    return value


def get_stipend_value(lines: list[str], i: int, trigger: str) -> str:
    """Extract value for a Stipend field."""
    line = lines[i].strip()
    value = re.sub(trigger + r"\s*", "", line, flags=re.I).strip().rstrip(":")
    # Remove label suffix that might be included
    value = re.sub(r"\s*\(INR\)\s*:?\s*$", "", value, flags=re.I).strip()
    return value


# ── Main PDF parser ────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path) -> dict:
    result = {k: "" for k in [
        "college_name", "admission_fee_total_inr",
        "year1_fee_inr", "year2_fee_inr", "year3_fee_inr",
        "year4_fee_inr", "year5_fee_inr",
        "stipend_year1_inr", "stipend_year2_inr", "stipend_year3_inr",
        "hostel_male", "hostel_female",
        "fee_deducted_reallocation_inr", "fee_reimbursed_if_resign_inr",
        "fee_forfeited_round2_inr",
        "nri_year1_usd", "nri_year2_usd", "nri_year3_usd",
        "nri_year4_usd", "nri_year5_usd",
        "annual_fee_inr",
    ]}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            lines = []
            for page in pdf.pages:
                lines.extend((page.extract_text() or "").splitlines())
    except Exception as e:
        log.error(f"  {pdf_path.name}: {e}")
        return result

    # ── Simple colon-split fields ─────────────────────────────────────────
    for ln in lines:
        s = ln.strip()
        if s.startswith("Name of the College:"):
            result["college_name"] = s.split(":", 1)[1].strip()
        elif "Annual Fees of Candidates(INR):" in s:
            result["annual_fee_inr"] = s.split(":", 1)[1].strip()
        elif re.match(r"Hostel facility for Male students\s*:", s):
            result["hostel_male"] = extract_yesno(s)
        elif re.match(r"Hostel facility for Female students\s*:", s):
            result["hostel_female"] = extract_yesno(s)

    # ── Multi-line fields ─────────────────────────────────────────────────
    inr_slots = [
        "admission_fee_total_inr",
        "year1_fee_inr", "year2_fee_inr", "year3_fee_inr",
        "year4_fee_inr", "year5_fee_inr",
    ]
    nri_slots = [
        "nri_year1_usd", "nri_year2_usd", "nri_year3_usd",
        "nri_year4_usd", "nri_year5_usd",
    ]
    ic = nc = 0

    for i, ln in enumerate(lines):
        s = ln.strip()

        # ── Amount fields ─────────────────────────────────────────────────
        if re.match(r"Amount to be [Pp]aid at the time of", s):
            value = get_value_for_amount_field(lines, i)

            # INR vs NRI determined by looking ahead for "NRI" in label lines
            lookahead = " ".join(
                lines[j].strip()
                for j in range(i+1, min(i+5, len(lines)))
            ).lower()
            is_nri = "nri" in lookahead

            if is_nri:
                if nc < len(nri_slots):
                    result[nri_slots[nc]] = value; nc += 1
            else:
                if ic < len(inr_slots):
                    result[inr_slots[ic]] = value; ic += 1

        # ── Stipend fields ────────────────────────────────────────────────
        elif re.match(r"Stipend Paid to the students I st Year", s):
            result["stipend_year1_inr"] = get_stipend_value(
                lines, i, r"Stipend Paid to the students I st Year")
        elif re.match(r"Stipend Paid to the students IInd Year", s):
            result["stipend_year2_inr"] = get_stipend_value(
                lines, i, r"Stipend Paid to the students IInd Year")
        elif re.match(r"Stipend Paid to the students IIIrd Year", s):
            result["stipend_year3_inr"] = get_stipend_value(
                lines, i, r"Stipend Paid to the students IIIrd Year")

        # ── Fee deducted on re-allocation ─────────────────────────────────
        elif re.match(r"The Amount of Fee to be deducted on", s):
            val = re.sub(r"The Amount of Fee to be deducted on\s*",
                         "", s, flags=re.I).strip()
            if not val:
                for j in range(i+1, min(i+6, len(lines))):
                    next_s = lines[j].strip()
                    if is_new_trigger(next_s): break
                    if re.search(r"Counseling.*INR\)", next_s, re.I):
                        m = re.search(r":\s*(.+)$", next_s)
                        if m:
                            v = m.group(1).strip()
                            if not is_new_trigger(v): val = v
                        break
            result["fee_deducted_reallocation_inr"] = val

        # ── Fee reimbursed if resign ──────────────────────────────────────
        elif re.match(r"The Amount of Fees To be reimbursed", s):
            val = re.sub(r"The Amount of Fees To be reimbursed\s*",
                         "", s, flags=re.I).strip()
            if not val:
                for j in range(i+1, min(i+5, len(lines))):
                    next_s = lines[j].strip()
                    if is_new_trigger(next_s): break
                    if re.search(r"Counseling.*INR\)", next_s, re.I):
                        m = re.search(r":\s*(.+)$", next_s)
                        if m:
                            v = m.group(1).strip()
                            if not is_new_trigger(v): val = v
                        break
            result["fee_reimbursed_if_resign_inr"] = val

        # ── Fee forfeited in round 2 ──────────────────────────────────────
        elif re.match(r"Amount to be forfeited in case of", s):
            val = ""
            for j in range(i, min(i+7, len(lines))):
                next_s = lines[j].strip()
                if re.search(r"Round of counselling period.*INR", next_s, re.I):
                    m = re.search(r":\s*(.+)$", next_s)
                    if m:
                        v = m.group(1).strip()
                        # Only accept if it's not the start of a new field
                        if not is_new_trigger(v):
                            val = v
                    else:
                        # Check next line — but not if it's a new trigger
                        if j+1 < len(lines):
                            nxt = lines[j+1].strip()
                            if not is_new_trigger(nxt) and re.search(r'\d|^$', nxt):
                                val = nxt if re.search(r'\d', nxt) else ""
                    break
            result["fee_forfeited_round2_inr"] = val

    return result


# ── Output columns ─────────────────────────────────────────────────────────

COLUMNS = [
    "institute_code", "college_name", "admission_fee_total_inr",
    "year1_fee_inr", "year2_fee_inr", "year3_fee_inr",
    "year4_fee_inr", "year5_fee_inr",
    "stipend_year1_inr", "stipend_year2_inr", "stipend_year3_inr",
    "hostel_male", "hostel_female",
    "fee_deducted_reallocation_inr", "fee_reimbursed_if_resign_inr",
    "fee_forfeited_round2_inr",
    "nri_year1_usd", "nri_year2_usd", "nri_year3_usd",
    "nri_year4_usd", "nri_year5_usd",
    "annual_fee_inr",
]


def main(pdf_dir=PDF_DIR, output=OUTPUT_CSV):
    pdf_dir = Path(pdf_dir)
    pdfs    = sorted(pdf_dir.glob("*.pdf"))

    if not pdfs:
        log.error(f"No PDFs found in {pdf_dir.resolve()}")
        log.error("Run mcc_download_pdfs_v4.py first.")
        return

    log.info(f"Found {len(pdfs)} PDFs in {pdf_dir.resolve()}")

    names = {}
    if Path(CODES_CSV).exists():
        df = pd.read_csv(CODES_CSV, dtype=str).fillna("")
        names = dict(zip(df["institute_code"], df["institute_name"]))

    rows = []
    for pdf_path in tqdm(pdfs, desc="Parsing PDFs", unit="PDF"):
        code   = pdf_path.stem
        fields = parse_pdf(pdf_path)
        fields["institute_code"] = code

        if code in names:
            csv_name = names[code]
            if (not fields["college_name"] or
                    len(fields["college_name"]) < len(csv_name) // 2):
                fields["college_name"] = csv_name

        rows.append(fields)

    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df.columns: df[c] = ""
    df = df[COLUMNS].sort_values("institute_code").reset_index(drop=True)
    df.to_csv(output, index=False, encoding="utf-8-sig")

    log.info(f"Saved {len(df)} rows → {output}")
    print(f"\n✓ Done! {len(df)} colleges → {output}")
    print(f"\nSample (first 3 rows, key columns):")
    print(df[["institute_code", "college_name",
              "admission_fee_total_inr", "hostel_male",
              "annual_fee_inr"]].head(3).to_string(index=False))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="MCC PDF Parser v2")
    p.add_argument("--pdf-dir", default=str(PDF_DIR))
    p.add_argument("--out",     default=OUTPUT_CSV)
    args = p.parse_args()
    main(pdf_dir=args.pdf_dir, output=args.out)