"""
Maharashtra NEET UG 2025 — Final Cutoff Aggregation
=====================================================
Joins three CSV files and builds the final college-wise
cutoff table with opening/closing ranks per category, quota and round.

Input files (all in same folder):
  1. Unique_Colleges.csv        — college master (Code, College, Branch, Type)
  2. Merit_List_Round3.csv      — candidate merit (State Merit Rank, Category Rank)
  3. All_Selection_Lists.csv    — allotment data  (AIR, Category, Quota, College, Round)

Output:
  Maharashtra_NEET_2025_Cutoffs.csv

Output columns:
  College_Name | College_Type | Branch | Category | Seat_Type | Admission_Round |
  Opening_Rank_AIR | Closing_Rank_AIR |
  Opening_State_Merit_Rank | Closing_State_Merit_Rank |
  Total_Seats_Filled |
  Opening_Category_Rank | Closing_Category_Rank

Usage:
  python build_cutoffs.py
  python build_cutoffs.py --folder /path/to/csvs --output out.csv
"""

import re
import argparse
from pathlib import Path

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# ROUND SORT ORDER  (CAP 1 → 2 → 3 → Stray 1 → 2 → 3)
# ─────────────────────────────────────────────────────────────────────────────
ROUND_ORDER = {
    'CAP Round 1'  : 1,
    'CAP Round 2'  : 2,
    'CAP Round 3'  : 3,
    'Stray Round 1': 4,
    'Stray Round 2': 5,
    'Stray Round 3': 6,
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_category_rank(raw):
    """
    'OBC-3'    → ('OBC',  3)
    'NT(D)-1'  → ('NT(D)',1)
    'EWS-2'    → ('EWS',  2)
    'OPEN'     → ('OPEN', None)
    ''  / NaN  → ('',     None)
    """
    s = str(raw).strip()
    if s in ('', 'nan', 'None'):
        return '', None
    m = re.match(r'^(.+?)-(\d+)$', s)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return s, None   # e.g. 'OPEN' with no numeric suffix


def first_nonempty(series):
    """Return first non-null, non-empty string value from a Series."""
    for v in series:
        if pd.notna(v) and str(v).strip() not in ('', 'nan'):
            return str(v).strip()
    return ''


def safe_int(val):
    """Convert to int, return None if NaN."""
    try:
        if pd.isna(val):
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def format_cat_rank(prefix, num):
    """Format as 'OBC-3'. Returns '' if either arg is missing."""
    if not prefix or prefix == 'OPEN' or pd.isna(num):
        return ''
    return f"{prefix}-{int(num)}"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Build Maharashtra NEET 2025 cutoff CSV from 3 input files'
    )
    parser.add_argument('--folder', '-f', default='.',
                        help='Folder with the 3 CSV files (default: current directory)')
    parser.add_argument('--output', '-o', default='../../../data/maharashtra_colleges/final_data/maharashtra_neet_2025_cutoffs.csv',
                        help='Output CSV path')
    args   = parser.parse_args()
    folder = Path(args.folder)

    print("\nMaharashtra NEET UG 2025 — Cutoff Builder")
    print("=" * 50)

    # ── 1. LOAD ────────────────────────────────────────────────────────────
    colleges  = pd.read_csv(folder / '../../../data/maharashtra_colleges/stray_round_colleges.csv',
                            dtype={'Code': str})
    merit     = pd.read_csv(folder / '../../../data/maharashtra_colleges/merit_list_round3.csv',
                            dtype={'CetCell Online Form No.': str})
    selection = pd.read_csv(folder / '../../../data/maharashtra_colleges/final_data/all_selection_lists.csv',
                            dtype={'CET_Form_No': str, 'College_Code': str})

    print(f"  Colleges     : {len(colleges):,} rows")
    print(f"  Merit List   : {len(merit):,} rows")
    print(f"  Selection    : {len(selection):,} rows across "
          f"{selection['Round'].nunique()} rounds")

    # ── 2. PROCESS MERIT LIST ──────────────────────────────────────────────
    merit = merit.rename(columns={
        'Sr.No'                    : 'State_Merit_Rank',
        'CetCell Online Form No.'  : 'CET_Form_No',
        'Category'                 : 'Category_Raw',
    })
    merit['CET_Form_No']       = merit['CET_Form_No'].astype(str).str.strip()
    merit['State_Merit_Rank']  = pd.to_numeric(merit['State_Merit_Rank'], errors='coerce')

    # Split 'OBC-1' → Merit_Category='OBC', Category_Rank=1
    parsed = merit['Category_Raw'].apply(parse_category_rank)
    merit['Merit_Category'] = [p[0] for p in parsed]
    merit['Category_Rank']  = pd.to_numeric([p[1] for p in parsed], errors='coerce')

    merit_slim = merit[['CET_Form_No', 'State_Merit_Rank',
                         'Merit_Category', 'Category_Rank']].copy()

    print(f"\n  Merit categories found : "
          f"{sorted(merit['Merit_Category'].replace('', 'OPEN').unique().tolist())}")

    # ── 3. PROCESS SELECTION LIST ──────────────────────────────────────────
    sel = selection.copy()
    sel['CET_Form_No']  = sel['CET_Form_No'].astype(str).str.strip()
    sel['College_Code'] = sel['College_Code'].astype(str).str.strip()
    sel['AIR']          = pd.to_numeric(sel['AIR'], errors='coerce')

    # Blank category = OPEN category candidate
    sel['Category'] = sel['Category'].fillna('').str.strip()
    sel['Category'] = sel['Category'].replace('', 'OPEN')

    # ── 4. JOIN: Selection ← Merit  (on CET_Form_No) ──────────────────────
    merged = sel.merge(merit_slim, on='CET_Form_No', how='left')

    matched = merged['State_Merit_Rank'].notna().sum()
    print(f"\n  Merit JOIN matches     : {matched:,} / {len(merged):,} rows "
          f"({matched/len(merged)*100:.1f}%)")
    if matched < len(merged) * 0.8:
        print("  ⚠  Less than 80% of selection rows matched in merit list.")
        print("     Check that CetCell Form No. columns match exactly.")

    # ── 5. JOIN: Merged ← Colleges  (on College_Code) ─────────────────────
    colleges = colleges.rename(columns={
        'Code'   : 'College_Code',
        'College': 'College_Full_Name',
    })
    colleges['College_Code'] = colleges['College_Code'].astype(str).str.strip()
    merged = merged.merge(colleges, on='College_Code', how='left')

    # Use full name from Unique_Colleges when available;
    # fall back to abbreviated name from selection list
    merged['College_Name_Final'] = (
        merged['College_Full_Name']
        .fillna(merged['College_Name'])
        .str.strip()
    )

    unmatched_colleges = merged[merged['College_Full_Name'].isna()]['College_Code'].unique()
    if len(unmatched_colleges) > 0:
        print(f"\n  ⚠  {len(unmatched_colleges)} college codes not in Unique_Colleges.csv "
              f"(abbreviated name used):")
        for c in sorted(unmatched_colleges)[:10]:
            abbr = merged[merged['College_Code']==c]['College_Name'].iloc[0]
            print(f"     Code {c} → '{abbr}'")

    # ── 6. AGGREGATE ──────────────────────────────────────────────────────
    group_keys = [
        'College_Code', 'College_Name_Final',
        'Category', 'Quota', 'Round',
        'Type', 'Branch',
    ]

    agg = merged.groupby(group_keys, as_index=False, sort=False, dropna=False).agg(
        Opening_Rank_AIR         = ('AIR',             'min'),
        Closing_Rank_AIR         = ('AIR',             'max'),
        Opening_State_Merit_Rank = ('State_Merit_Rank','min'),
        Closing_State_Merit_Rank = ('State_Merit_Rank','max'),
        _Opening_Cat_Rank_Num    = ('Category_Rank',   'min'),
        _Closing_Cat_Rank_Num    = ('Category_Rank',   'max'),
        _Merit_Category          = ('Merit_Category',
                                    lambda x: first_nonempty(x)),
        Total_Seats_Filled       = ('AIR',             'count'),
    )

    # ── 7. FORMAT CATEGORY RANK STRINGS ───────────────────────────────────
    # e.g. OBC min_rank=3 → "OBC-3"  |  OPEN → "" (no category rank)
    agg['Opening_Category_Rank'] = agg.apply(
        lambda r: format_cat_rank(r['_Merit_Category'], r['_Opening_Cat_Rank_Num']),
        axis=1
    )
    agg['Closing_Category_Rank'] = agg.apply(
        lambda r: format_cat_rank(r['_Merit_Category'], r['_Closing_Cat_Rank_Num']),
        axis=1
    )

    # ── 8. CLEAN & SORT ────────────────────────────────────────────────────
    # Integer columns (State Merit Rank can be NaN for non-registered candidates)
    for col in ['Opening_Rank_AIR', 'Closing_Rank_AIR', 'Total_Seats_Filled']:
        agg[col] = pd.to_numeric(agg[col], errors='coerce').astype('Int64')

    for col in ['Opening_State_Merit_Rank', 'Closing_State_Merit_Rank']:
        agg[col] = pd.to_numeric(agg[col], errors='coerce').astype('Int64')

    # Round sort key
    agg['_round_order'] = agg['Round'].map(ROUND_ORDER).fillna(99)

    # Sort: Branch → College Name → Category → Quota → Round
    agg = agg.sort_values(
        ['Branch', 'College_Name_Final', 'Category', 'Quota', '_round_order'],
        ascending=True,
        ignore_index=True
    )

    # ── 9. SELECT & RENAME FINAL COLUMNS ──────────────────────────────────
    final = agg[[
        'College_Name_Final',
        'Type',
        'Branch',
        'Category',
        'Quota',
        'Round',
        'Opening_Rank_AIR',
        'Closing_Rank_AIR',
        'Opening_State_Merit_Rank',
        'Closing_State_Merit_Rank',
        'Total_Seats_Filled',
        'Opening_Category_Rank',
        'Closing_Category_Rank',
    ]].rename(columns={
        'College_Name_Final'       : 'College_Name',
        'Type'                     : 'College_Type',
        'Quota'                    : 'Seat_Type',
        'Round'                    : 'Admission_Round',
        'Opening_Rank_AIR'         : 'Opening_Rank_AIR',
        'Closing_Rank_AIR'         : 'Closing_Rank_AIR',
        'Opening_State_Merit_Rank' : 'Opening_State_Merit_Rank',
        'Closing_State_Merit_Rank' : 'Closing_State_Merit_Rank',
    })

    # ── 10. SAVE ───────────────────────────────────────────────────────────
    out = Path(args.output)
    final.to_csv(out, index=False, encoding='utf-8-sig')

    # ── 11. SUMMARY ────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  Cutoff table complete!")
    print(f"{'='*50}")
    print(f"  Total rows             : {len(final):,}")
    print(f"  Output file            : {out}")
    print(f"\n  Rows per round:")
    for rnd, cnt in final['Admission_Round'].value_counts().sort_index().items():
        print(f"    {rnd:<22}: {cnt:>5,}")
    print(f"\n  Rows per branch:")
    for br, cnt in final['Branch'].value_counts().items():
        print(f"    {br:<8}: {cnt:>5,}")
    print(f"\n  Unique colleges        : {final['College_Name'].nunique():,}")
    print(f"  Unique categories      : {sorted(final['Category'].unique().tolist())}")
    print(f"  Rows with Category Rank: "
          f"{(final['Opening_Category_Rank'] != '').sum():,}")
    print(f"\n  Preview (first 10 rows):")
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 200)
    print(final.head(10).to_string(index=False))


if __name__ == '__main__':
    main()