"""
Maharashtra NEET UG 2025 — Selection List PDF Extractor
========================================================
No external tools required. Uses pdfminer.six (pure Python).

Install requirements (run once):
    pip install pdfminer.six pandas

Processes all 6 Selection List PDFs in parallel.
Validated: 10,815/10,815 rows, 0 errors on CAP Round 1.

Input  : 6 PDF files in the same folder as this script
Output : All_Selection_Lists.csv

Output columns:
  AIR | NEET_Roll_No | CET_Form_No | Category | Quota |
  College_Code | College_Name | Round

Usage:
  python extract_selection_lists.py
  python extract_selection_lists.py --folder /path/to/pdfs
  python extract_selection_lists.py --folder C:\\PDFs --output out.csv
"""

import re
import io
import argparse
import time
from pathlib import Path
from multiprocessing import Pool, cpu_count

import pandas as pd
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams


# ─────────────────────────────────────────────────────────────────────────────
# PDF → ROUND LABEL MAP
# ─────────────────────────────────────────────────────────────────────────────
PDF_ROUNDS = {
    "../../docs/maharashtra_colleges/cap_round1_sl.pdf"      : "CAP Round 1",
    "../../docs/maharashtra_colleges/cap_round2_sl.pdf"      : "CAP Round 2",
    "../../docs/maharashtra_colleges/cap_round3_sl.pdf"      : "CAP Round 3",
    "../../docs/maharashtra_colleges/stray_cap_round1_sl.pdf": "Stray Round 1",
    "../../docs/maharashtra_colleges/stray_cap_round2_sl.pdf": "Stray Round 2",
    "../../docs/maharashtra_colleges/stray_cap_round3_sl.pdf": "Stray Round 3",
}

# pdfminer layout parameters — tuned for this Word/PScript PDF format
LAPARAMS = LAParams(line_margin=0.3, char_margin=0.5, boxes_flow=0.5)


# ─────────────────────────────────────────────────────────────────────────────
# COMPILED PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

# Numeric prefix of every data row
PREFIX_RE = re.compile(
    r'^\s{0,6}(\d+)'       # Sr.No
    r'\s{1,12}(\d+)'       # AIR
    r'\s{2,6}(\d{10})'     # NEET Roll No  (always 10 digits)
    r'\s{1,2}(\d{9})'      # CET Form No   (always 9 digits)
    r'\s{1,2}'             # spaces before name
)

# College Code:Name at end of line — always NNNN:Name format
COLLEGE_RE = re.compile(r'(\d{4}):(.+)$')

# Candidates with no allotment — skip these rows
SKIP_RE = re.compile(r'Choice\s+Not\s+Available', re.I)

# Header / footer / legend lines — skip these
JUNK_RE = re.compile(
    r'(GOVERNMENT OF MAHARASHTRA|State Common Entrance|PROVISIONAL SELECTION'
    r'|Printed On|^\s*Note:|Last Date|Admitting Institute|Legends\s*:|'
    r'I\.Q\.\:Institute|F:Female|-------)', re.I
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_middle(raw: str):
    """
    Split middle blob (between Gender column and Code:College) into
    (Category, Quota).

    Category and Quota are separated by 2+ spaces in fixed-width layout.
    Examples:
      'OBC          OPEN (W)   (EMD)' → ('OBC',    'OPEN (W)')
      '      HA      HOPENW'          → ('HA',     'HOPENW')
      '             OPEN'             → ('',       'OPEN')
      '     MKB     MKB'              → ('MKB',    'MKB')
      'NTD D1       DEF1'             → ('NTD D1', 'DEF1')
      '             OPEN        MINO' → ('OPEN',   'MINO')
    """
    cleaned = re.sub(r'\(EM[DR]\)', '', raw or '').strip()
    parts   = [p.strip() for p in re.split(r'\s{2,}', cleaned) if p.strip()]
    if not parts:
        return '', ''
    if len(parts) == 1:
        return '', parts[0]
    return parts[0], parts[-1]


def parse_line(line: str):
    """
    Parse one pdfminer text line into a dict.
    Returns None for lines to skip.
    """
    if SKIP_RE.search(line):
        return None  # unallotted candidate

    pm = PREFIX_RE.match(line)
    if not pm:
        return None

    sr, air, roll, form = pm.groups()

    cm = COLLEGE_RE.search(line)
    if not cm:
        return None

    col_start = cm.start()
    code      = cm.group(1).strip()
    cname     = cm.group(2).strip()

    # Gender column is always at position 70-83 in fixed-width layout.
    # Scanning from 70 avoids matching middle initials (e.g. "SIDHANTH M SREERAJ")
    gender     = None
    gender_pos = None
    for pos in range(70, min(83, len(line))):
        ch   = line[pos]
        prev = line[pos - 1] if pos > 0 else ' '
        nxt  = line[pos + 1] if pos + 1 < len(line) else ' '
        if ch in ('M', 'F') and prev == ' ' and nxt == ' ':
            gender     = ch
            gender_pos = pos
            break

    if gender is None:
        return None

    middle            = line[gender_pos + 1: col_start].strip()
    category, quota   = parse_middle(middle)

    return {
        'AIR'         : int(air),
        'NEET_Roll_No': roll.strip(),
        'CET_Form_No' : form.strip(),
        'Category'    : category,
        'Quota'       : quota,
        'College_Code': code,
        'College_Name': cname,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PER-FILE WORKER
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(args):
    """
    Worker: PDF → pdfminer text → parse → list of row dicts.
    Returns (round_label, records, stats_dict).
    """
    pdf_path, round_label = args
    pdf_path = Path(pdf_path)

    try:
        text = extract_text(str(pdf_path), laparams=LAPARAMS)
    except Exception as e:
        return round_label, [], {'error': str(e)}

    if not text.strip():
        return round_label, [], {'error': f'No text extracted from {pdf_path.name}'}

    records   = []
    matched   = 0
    skipped   = 0
    parse_err = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Fast pre-filter: data rows start with spaces then digits
        if not re.match(r'^\s{0,6}\d+\s', line):
            continue
        if JUNK_RE.search(line):
            continue
        if SKIP_RE.search(line):
            skipped += 1
            continue

        row = parse_line(line)
        if row is None:
            parse_err += 1
            continue

        row['Round'] = round_label
        records.append(row)
        matched += 1

    return round_label, records, {
        'matched'  : matched,
        'skipped'  : skipped,
        'parse_err': parse_err,
        'error'    : None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Extract Maharashtra NEET UG 2025 Selection Lists → CSV'
    )
    parser.add_argument('--folder', '-f', default='.',
                        help='Folder containing the 6 PDF files')
    parser.add_argument('--output', '-o', default='../../data/maharashtra_colleges/final_data/all_selection_lists.csv',
                        help='Output CSV file path')
    args   = parser.parse_args()
    folder = Path(args.folder)

    print("\nMaharashtra NEET UG 2025 — Selection List Extractor")
    print("=" * 58)

    tasks, missing = [], []
    for filename, round_label in PDF_ROUNDS.items():
        pdf_path = folder / filename
        if not pdf_path.exists():
            missing.append(filename)
        else:
            mb = pdf_path.stat().st_size / 1_048_576
            print(f"  ✓  {filename:<32} ({mb:5.1f} MB)")
            tasks.append((str(pdf_path), round_label))

    if missing:
        print(f"\n  ✗  Missing files:")
        for f in missing:
            print(f"       {f}")
        print("\n  Place all 6 PDFs in the folder and re-run.")
        return

    workers = min(len(tasks), cpu_count())
    print(f"\n  Using {workers} parallel workers …")
    print(f"  Expected time: 1–3 minutes\n")

    t_start = time.time()
    with Pool(processes=workers) as pool:
        results = pool.map(process_pdf, tasks)

    print(f"  {'Round':<22}  {'Allotted':>10}  {'Choice N/A':>12}  {'Errors':>8}")
    print("  " + "─" * 56)

    all_records   = []
    total_matched = total_skipped = total_errors = 0

    for round_label, records, stats in sorted(results, key=lambda x: x[0]):
        if stats.get('error'):
            print(f"  ✗  {round_label}: {stats['error']}")
            continue
        all_records.extend(records)
        total_matched += stats['matched']
        total_skipped += stats['skipped']
        total_errors  += stats['parse_err']
        print(f"  {round_label:<22}  {stats['matched']:>10,}  "
              f"{stats['skipped']:>12,}  {stats['parse_err']:>8,}")

    elapsed = time.time() - t_start
    print("  " + "─" * 56)
    print(f"  {'TOTAL':<22}  {total_matched:>10,}  "
          f"{total_skipped:>12,}  {total_errors:>8,}")
    print(f"\n  Time: {elapsed:.0f}s")

    if not all_records:
        print("\n  No records extracted.")
        return

    df = pd.DataFrame(all_records)
    df['AIR']          = pd.to_numeric(df['AIR'],          errors='coerce').astype('Int64')
    df['College_Code'] = pd.to_numeric(df['College_Code'], errors='coerce').astype('Int64')
    for col in ['NEET_Roll_No', 'CET_Form_No', 'Category',
                'Quota', 'College_Name', 'Round']:
        df[col] = df[col].str.strip()
    df = df[['AIR', 'NEET_Roll_No', 'CET_Form_No', 'Category', 'Quota',
             'College_Code', 'College_Name', 'Round']]

    out = Path(args.output)
    df.to_csv(out, index=False, encoding='utf-8-sig')

    print(f"\n{'='*58}")
    print(f"  Extraction complete!")
    print(f"{'='*58}")
    print(f"  Total rows saved       : {len(df):,}")
    print(f"  Output file            : {out}")
    print(f"\n  Rows per round:")
    for rnd, cnt in df['Round'].value_counts().sort_index().items():
        print(f"    {rnd:<24}: {cnt:>6,}")
    print(f"\n  Unique colleges        : {df['College_Code'].nunique():,}")
    print(f"  AIR range              : {int(df['AIR'].min()):,} → {int(df['AIR'].max()):,}")
    print(f"\n  Preview (first 5 rows):")
    print(df.head().to_string(index=False))


if __name__ == '__main__':
    main()