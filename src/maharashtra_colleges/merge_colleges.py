"""
Merge CAP Round CSVs and extract unique colleges.

Input files (all in same folder as script):
    CAP_Round1_Colleges.csv
    CAP_Round2_Colleges.csv
    CAP_Round3_Colleges.csv
    Stray_Round_Colleges.csv

Output:
    Unique_Colleges.csv  — deduplicated on Code + Branch + Type
"""

import pandas as pd
import os

# ── CONFIG ────────────────────────────────────────────────────────────────────
INPUT_FILES = [
    "../../data/maharashtra_colleges/cap_round1_colleges.csv",
    "../../data/maharashtra_colleges/cap_round2_colleges.csv",
    "../../data/maharashtra_colleges/cap_round3_colleges.csv",
    "../../data/maharashtra_colleges/stray_round_colleges.csv",
]
OUTPUT_CSV = "../../data/maharashtra_colleges/stray_round_colleges.csv"
# ─────────────────────────────────────────────────────────────────────────────

def main():
    frames = []

    for f in INPUT_FILES:
        if not os.path.exists(f):
            print(f"[WARN] File not found, skipping: {f}")
            continue
        df = pd.read_csv(f, dtype=str)
        df.columns = df.columns.str.strip()          # clean any whitespace in headers
        df = df.fillna("").applymap(str.strip)       # strip cell whitespace
        frames.append(df)
        print(f"[INFO] Loaded {len(df):>4} rows from {f}")

    if not frames:
        print("[ERROR] No input files found. Check filenames and folder.")
        return

    combined = pd.concat(frames, ignore_index=True)
    print(f"\n[INFO] Total rows across all files : {len(combined)}")

    # Deduplicate: same Code + Branch + Type = same college
    unique = combined.drop_duplicates(subset=["Code", "Branch", "Type"])
    unique = unique.sort_values(["Branch", "Type", "Code"])
    unique.reset_index(drop=True, inplace=True)

    unique.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"[INFO] Unique colleges after dedup  : {len(unique)}")
    print(f"[INFO] Duplicates removed           : {len(combined) - len(unique)}")
    print(f"\n[SUCCESS] Saved to '{OUTPUT_CSV}'\n")

    print("Breakdown:")
    print(unique.groupby(["Branch", "Type"]).size().to_string())

if __name__ == "__main__":
    main()