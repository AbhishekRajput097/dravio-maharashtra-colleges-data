"""
Maharashtra NEET UG 2025 — Final Cutoff Aggregation  v2
=========================================================
FIX v2: JOIN on NEET_Roll_No instead of CET_Form_No.
        Resolves 1,032 rows where the candidate has different
        CET Form Numbers in the selection list vs merit list.

Input files (all in same folder):
  1. Unique_Colleges.csv
  2. Merit_List_Round3.csv
  3. All_Selection_Lists.csv

Output: Maharashtra_NEET_2025_Cutoffs_New.csv
"""

import re
import argparse
from pathlib import Path
import pandas as pd


ROUND_ORDER = {
    'CAP Round 1': 1, 'CAP Round 2': 2, 'CAP Round 3': 3,
    'Stray Round 1': 4, 'Stray Round 2': 5, 'Stray Round 3': 6,
}

CATEGORY_NORM = {
    'NT(B)': 'NTB', 'NT(C)': 'NTC', 'NT(D)': 'NTD', 'VJ(A)': 'VJA',
}


def parse_cat_rank(raw):
    s = str(raw).strip()
    if s in ('', 'nan', 'None'): return '', None
    m = re.match(r'^(.+?)-(\d+)$', s)
    return (m.group(1).strip(), int(m.group(2))) if m else (s, None)


def first_nonempty(series):
    for v in series:
        if pd.notna(v) and str(v).strip() not in ('', 'nan'):
            return str(v).strip()
    return ''


def format_cat_rank(prefix, num):
    if not prefix or prefix == 'OPEN' or pd.isna(num):
        return ''
    # Normalise NT(B)→NTB, NT(C)→NTC, NT(D)→NTD to match selection list naming
    normalised = CATEGORY_NORM.get(prefix, prefix)
    return f"{normalised}-{int(num)}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', '-f', default='.')
    parser.add_argument('--output', '-o', default='../../../data/maharashtra_colleges/final_data/maharashtra_neet_2025_cutoffs_new.csv')
    args   = parser.parse_args()
    folder = Path(args.folder)

    print("\nMaharashtra NEET UG 2025 — Cutoff Builder  v2")
    print("=" * 52)

    # ── 1. LOAD ────────────────────────────────────────────────────────
    colleges  = pd.read_csv(folder / '../../../data/maharashtra_colleges/stray_round_colleges.csv',
                            dtype={'Code': str}, encoding='utf-8-sig')
    merit     = pd.read_csv(folder / '../../../data/maharashtra_colleges/merit_list_round3.csv',
                            dtype={'CetCell Online Form No.': str,
                                   'NEET ROLL NO': str}, encoding='utf-8-sig')
    selection = pd.read_csv(folder / '../../../data/maharashtra_colleges/final_data/all_selection_lists.csv',
                            dtype={'CET_Form_No': str, 'College_Code': str,
                                   'NEET_Roll_No': str}, encoding='utf-8-sig')

    print(f"  Colleges     : {len(colleges):,}")
    print(f"  Merit List   : {len(merit):,}")
    print(f"  Selection    : {len(selection):,}")

    # ── 2. PROCESS MERIT LIST ──────────────────────────────────────────
    merit = merit.rename(columns={
        'Sr.No'                   : 'State_Merit_Rank',
        'CetCell Online Form No.' : 'CET_Form_No_Merit',
        'NEET ROLL NO'            : 'NEET_Roll_No',
        'Category'                : 'Category_Raw',
    })
    merit['NEET_Roll_No']      = merit['NEET_Roll_No'].str.strip()
    merit['State_Merit_Rank']  = pd.to_numeric(merit['State_Merit_Rank'], errors='coerce')

    parsed = merit['Category_Raw'].apply(parse_cat_rank)
    merit['Merit_Category'] = [p[0] for p in parsed]
    merit['Category_Rank']  = pd.to_numeric([p[1] for p in parsed], errors='coerce')

    # Keep one row per Roll No (there should only be one, but just in case)
    merit_slim = merit[['NEET_Roll_No', 'State_Merit_Rank',
                         'Merit_Category', 'Category_Rank']].drop_duplicates('NEET_Roll_No')

    # ── 3. PROCESS SELECTION LIST ──────────────────────────────────────
    sel = selection.copy()
    sel['NEET_Roll_No'] = sel['NEET_Roll_No'].str.strip()
    sel['College_Code'] = sel['College_Code'].str.strip()
    sel['AIR']          = pd.to_numeric(sel['AIR'], errors='coerce')
    sel['Category']     = sel['Category'].fillna('').str.strip().replace('', 'OPEN')

    # ── 4. JOIN on NEET_Roll_No  ← THE FIX ────────────────────────────
    #    Previous version joined on CET_Form_No, which missed 1,032 candidates
    #    whose Form No. was updated between selection and merit list.
    merged = sel.merge(merit_slim, on='NEET_Roll_No', how='left')

    matched  = merged['State_Merit_Rank'].notna().sum()
    print(f"\n  Merit JOIN (by Roll No) : {matched:,} / {len(merged):,} "
          f"({matched/len(merged)*100:.1f}%)")
    print(f"  Still unmatched        : {len(merged)-matched:,} "
          f"(genuine gaps — I.Q./NRI/ORP etc.)")

    # ── 5. JOIN COLLEGES ───────────────────────────────────────────────
    colleges = colleges.rename(columns={'Code': 'College_Code',
                                        'College': 'College_Full_Name'})
    colleges['College_Code'] = colleges['College_Code'].str.strip()
    merged = merged.merge(colleges, on='College_Code', how='left')
    merged['College_Name_Final'] = (merged['College_Full_Name']
                                    .fillna(merged['College_Name']).str.strip())

    # ── 6. AGGREGATE ───────────────────────────────────────────────────
    group_keys = ['College_Code', 'College_Name_Final',
                  'Category', 'Quota', 'Round', 'Type', 'Branch']

    agg = merged.groupby(group_keys, as_index=False, sort=False,
                         dropna=False).agg(
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

    # ── 7. FORMAT CATEGORY RANK ────────────────────────────────────────
    agg['Opening_Category_Rank'] = agg.apply(
        lambda r: format_cat_rank(r['_Merit_Category'], r['_Opening_Cat_Rank_Num']), axis=1)
    agg['Closing_Category_Rank'] = agg.apply(
        lambda r: format_cat_rank(r['_Merit_Category'], r['_Closing_Cat_Rank_Num']), axis=1)
    agg['Opening_Category_Rank'] = agg['Opening_Category_Rank'].fillna('')
    agg['Closing_Category_Rank'] = agg['Closing_Category_Rank'].fillna('')

    # ── 8. CLEAN & SORT ────────────────────────────────────────────────
    for col in ['Opening_Rank_AIR', 'Closing_Rank_AIR', 'Total_Seats_Filled']:
        agg[col] = pd.to_numeric(agg[col], errors='coerce').astype('Int64')
    for col in ['Opening_State_Merit_Rank', 'Closing_State_Merit_Rank']:
        agg[col] = pd.to_numeric(agg[col], errors='coerce').astype('Int64')

    agg['_round_order'] = agg['Round'].map(ROUND_ORDER).fillna(99)
    agg = agg.sort_values(
        ['Branch', 'College_Name_Final', 'Category', 'Quota', '_round_order'],
        ignore_index=True
    )

    # ── 9. FINAL COLUMNS ───────────────────────────────────────────────
    final = agg[[
        'College_Name_Final', 'Type', 'Branch', 'Category', 'Quota',
        'Round', 'Opening_Rank_AIR', 'Closing_Rank_AIR',
        'Opening_State_Merit_Rank', 'Closing_State_Merit_Rank',
        'Total_Seats_Filled', 'Opening_Category_Rank', 'Closing_Category_Rank',
    ]].rename(columns={
        'College_Name_Final' : 'College_Name',
        'Type'               : 'College_Type',
        'Quota'              : 'Seat_Type',
        'Round'              : 'Admission_Round',
    })

    out = Path(args.output)
    final.to_csv(out, index=False, encoding='utf-8-sig')

    # ── 10. SUMMARY ────────────────────────────────────────────────────
    print(f"\n{'='*52}")
    print(f"  Cutoff table complete!")
    print(f"{'='*52}")
    print(f"  Total rows             : {len(final):,}")
    print(f"  Output file            : {out}")
    print(f"\n  Rows per round:")
    for rnd, cnt in final['Admission_Round'].value_counts().sort_index().items():
        print(f"    {rnd:<24}: {cnt:>5,}")
    print(f"\n  Rows with State Rank   : "
          f"{final['Opening_State_Merit_Rank'].notna().sum():,} / {len(final):,}")
    print(f"  Rows with Cat. Rank    : "
          f"{(final['Opening_Category_Rank'] != '').sum():,}")
    print(f"\n  Verification (should both be 0):")
    print(f"    Opening AIR > Closing AIR  : "
          f"{(final['Opening_Rank_AIR'] > final['Closing_Rank_AIR']).sum()}")
    print(f"    Opening SMR > Closing SMR  : "
          f"{(final['Opening_State_Merit_Rank'] > final['Closing_State_Merit_Rank']).sum()}")
    print(f"\n  Preview (first 5 rows):")
    print(final.head().to_string(index=False))


if __name__ == '__main__':
    main()