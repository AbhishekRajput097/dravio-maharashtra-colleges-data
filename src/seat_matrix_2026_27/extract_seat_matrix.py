"""
Extract NEET UG 2026-27 seat matrix data from the NMC PDF
(PublicNotice_Merged_seatmatrix_Secretary_covering.pdf).

Produces two CSV files:

1. increased_seats.csv
   Columns: Sr.No, State, College Code, College Name, Mgmt, Seats renewed,
            Seats increased, Total
   Only colleges where "Seats increased" has a non-blank / non-zero value
   (i.e. colleges whose intake capacity went up for AY 2026-27).

2. statewise_seat_matrix.csv
   The summary table titled:
   "Seat Matrix for AY:2026-27 for UG Course (MBBS) excluding seats of INIs
   (Statewise)* as on 13.07.2026"
   Flattened into a clean single-header CSV with columns:
   Sr.No, State, Govt No of College, Govt Seats renewed, Govt Seats increased,
   Govt Total Seats, Pvt No of College, Pvt Seats renewed, Pvt Seats increased,
   Pvt Total Seats, Total No of College, Total Seats renewed,
   Total Seats increased, Total Total Seats

Usage:
    pip install pdfplumber --break-system-packages
    python extract_seat_matrix.py PublicNotice_Merged_seatmatrix_Secretary_covering.pdf
"""

import sys
import csv
import re
import pdfplumber

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "../../docs/seat_matrix_2026_27/publicnotice_merged_seatmatrix_secretary_covering.pdf"

COLLEGE_TABLE_TITLE = "seat matrix for ay:2026-27 for ug course (mbbs) excluding seats of inis as on"
STATEWISE_TABLE_TITLE = "seat matrix for ay:2026-27 for ug course (mbbs) excluding seats of inis (statewise)"


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def clean_cell(c):
    return re.sub(r"\s+", " ", str(c or "")).strip()


def is_college_header_row(row):
    joined = " ".join(norm(c).lower() for c in row if c)
    hints = ["s. no", "state", "college code", "college name", "mgmt", "seats renewed", "seats increased", "total"]
    return sum(1 for h in hints if h in joined) >= 5


def is_statewise_header_row(row):
    joined = " ".join(norm(c).lower() for c in row if c)
    return "sr. no" in joined and "state" in joined and "government" in joined


def build_college_csv(pdf, out_path="../../data/seat_matrix_2026_27/increased_seats.csv"):
    out_header = ["Sr.No", "State", "College Code", "College Name", "Mgmt",
                  "Seats renewed", "Seats increased", "Total"]
    rows_written = 0
    total_rows_seen = 0

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(out_header)

        for page in pdf.pages:
            text = (page.extract_text() or "").lower()
            if COLLEGE_TABLE_TITLE not in text:
                continue

            for table in page.extract_tables():
                for row in table:
                    row = [clean_cell(c) for c in row]
                    if not any(row):
                        continue
                    if is_college_header_row(row):
                        continue
                    # Skip title / stray rows that aren't real data rows
                    if not row[0].isdigit():
                        continue

                    total_rows_seen += 1
                    if len(row) < 8:
                        row = row + [""] * (8 - len(row))

                    seats_increased = row[6]
                    if seats_increased and seats_increased not in ("0", "-", "NA", "N/A"):
                        writer.writerow(row[:8])
                        rows_written += 1

    print(f"[increased_seats.csv] Scanned {total_rows_seen} college rows; "
          f"wrote {rows_written} rows with increased seats.")
    return rows_written


def build_statewise_csv(pdf, out_path="../../data/seat_matrix_2026_27/statewise_seat_matrix.csv"):
    out_header = [
        "Sr.No", "State",
        "Govt No of College", "Govt Seats renewed", "Govt Seats increased", "Govt Total Seats",
        "Pvt No of College", "Pvt Seats renewed", "Pvt Seats increased", "Pvt Total Seats",
        "Total No of College", "Total Seats renewed", "Total Seats increased", "Total Total Seats",
    ]
    rows_written = 0

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(out_header)

        for page in pdf.pages:
            text = (page.extract_text() or "").lower()
            if STATEWISE_TABLE_TITLE not in text:
                continue

            for table in page.extract_tables():
                for row in table:
                    row = [clean_cell(c) for c in row]
                    if not any(row):
                        continue
                    if is_statewise_header_row(row) or len(row) < 14:
                        continue
                    if "grand total" in norm(row[1]).lower():
                        writer.writerow(row[:14])
                        rows_written += 1
                        continue
                    if not row[0].isdigit():
                        continue
                    writer.writerow(row[:14])
                    rows_written += 1

    print(f"[statewise_seat_matrix.csv] Wrote {rows_written} rows.")
    return rows_written


def main():
    print(f"Reading: {PDF_PATH}")
    with pdfplumber.open(PDF_PATH) as pdf:
        print(f"Total pages: {len(pdf.pages)}\n")
        build_college_csv(pdf, "../../data/seat_matrix_2026_27/increased_seats.csv")
        build_statewise_csv(pdf, "../../data/seat_matrix_2026_27/statewise_seat_matrix.csv")


if __name__ == "__main__":
    main()