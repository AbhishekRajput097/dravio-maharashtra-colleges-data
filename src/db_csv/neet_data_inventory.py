"""
NEET Counseling - Category Mapping Pipeline
=============================================

WHAT THIS DOES
---------------
Every time new state CSVs (Cutoff / Seats / Fees) land in DATA_FOLDER, run this
script. It will:

1. Scan every CSV, extract every unique raw category string found.
2. Check each raw category against the persistent MASTER mapping file.
   - Already known (approved) -> skip, nothing to do.
   - Already known (needs_review) -> skip, already flagged, waiting on you.
   - Brand new -> run it through the rule-based auto-classifier, add it to
     the master file with status='needs_review' and a best-guess canonical
     category + confidence.
3. Save the updated master file (this is your permanent, growing source of truth).
4. Save a separate review_queue.csv containing ONLY the rows that currently
   need human review (new or previously unresolved) - so your team never has
   to re-look at the same file, just the delta.
5. Regenerate frontend_category_mapping.json from ONLY approved rows - this
   is the file you hand to the frontend team. It always reflects only
   verified, clean data.

HOW YOUR TEAM USES IT DAY TO DAY
----------------------------------
1. Drop new state CSV(s) into DATA_FOLDER.
2. Run:  python category_mapping_pipeline.py
3. Open review_queue.csv, check rows where status=needs_review.
   - If the suggested_canonical looks right -> just change status to
     'approved' in master_category_mapping.csv (or approve in bulk, see
     approve_all_high_confidence() below).
   - If wrong -> correct suggested_canonical / modifier / is_pwd, then set
     status to 'approved'.
4. Re-run the script (or just run export_frontend_mapping() alone) to
   regenerate frontend_category_mapping.json with the corrections folded in.

WHY A MASTER FILE INSTEAD OF RECOMPUTING EVERY TIME
------------------------------------------------------
Once your team approves "SEBC -> OBC" for Maharashtra, that decision is
permanent and reused automatically for every future Maharashtra file. You
only ever review truly NEW raw strings, not the same ones over and over.
"""

import os
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# ==========================================================
# CONFIGURATION
# ==========================================================

DATA_FOLDER = "DB-CSV"                     # root folder where new state CSVs land
MASTER_MAPPING_FILE = "../../data/db_csv/master_category_mapping.csv"
REVIEW_QUEUE_FILE = "../../data/db_csv/review_queue.csv"
FRONTEND_EXPORT_FILE = "../../data/db_csv/frontend_category_mapping.json"

CATEGORY_COLUMNS = ["category", "category_raw_code", "category_full_name"]

# The 6-7 canonical buckets exposed to frontend. Keep this list short on purpose.
CANONICAL_CATEGORIES = [
    "GENERAL",
    "EWS",
    "OBC",
    "SC",
    "ST",
    "NT",          # Nomadic / Denotified Tribes - constitutionally distinct from OBC (Maharashtra etc.)
    "MINORITY",
    "NRI",
    "MANAGEMENT",
]

# ==========================================================
# RULE-BASED AUTO-CLASSIFIER
# ==========================================================
# Ordered list of (regex pattern, canonical_category, confidence).
# Matched against a normalized (upper, stripped, punctuation-lightened) string.
# First match wins, so put more specific patterns before generic ones.

PWD_PATTERN = re.compile(r"\b(PWD|PH|PW?D|DISAB)\b", re.IGNORECASE)

RULES = [
    # --- Nomadic / Denotified tribes (must come before generic OBC rules) ---
    (re.compile(r"\bVJ|\bNTB\b|\bNTC\b|\bNTD\b|\bNT\b|NOMADIC|DENOTIFI", re.I), "NT", "HIGH"),

    # --- NRI ---
    (re.compile(r"\bNRI\b|\bOCI\b", re.I), "NRI", "HIGH"),

    # --- Management / Institutional quota ---
    (re.compile(r"MANAGEMENT|MGT\s*QUOTA|\bMQ\b|INSTITUTIONAL|DEEMED|PAID\s*QUOTA|\bI\.?Q\.?\b", re.I), "MANAGEMENT", "MEDIUM"),

    # --- EWS ---
    (re.compile(r"\bEWS\b|\bEW\b(?!ITH)", re.I), "EWS", "HIGH"),

    # --- Minority / Religious community ---
    (re.compile(r"MINORITY|MUSLIM|CHRISTIAN|\bRC\d\b|RELIGIOUS\s*COMMUNITY", re.I), "MINORITY", "MEDIUM"),

    # --- SC ---
    (re.compile(r"\bSC\d?\b(?!HEDULE)", re.I), "SC", "HIGH"),

    # --- ST ---
    (re.compile(r"\bST\d?\b", re.I), "ST", "HIGH"),

    # --- OBC / Backward class family (broad, keep after NT/SC/ST/EWS checks) ---
    (re.compile(r"\bOBC\b|\bBC[ABCDE]?\b|SEBC|\bSEB\b|SOBC|\bMBC\b|\bEBC\b|\bDNC\b|BACKWARD", re.I), "OBC", "HIGH"),

    # --- General / Open / Unreserved ---
    (re.compile(r"\bGENERAL\b|\bOPEN\b|\bOPN\b|\bOP\b|\bOC\b|\bUR\b|UNRESERVED", re.I), "GENERAL", "HIGH"),
]


def normalize(raw: str) -> str:
    """Light normalization for matching only - we never overwrite the display string."""
    return re.sub(r"[^A-Za-z0-9./\- ]", " ", str(raw)).strip()


PLAIN_SYNONYMS = {
    "GENERAL", "OPEN", "OPN", "OP", "OC", "UR", "UNRESERVED",
    "EW", "EWS", "SC", "ST", "OBC", "BC", "NRI", "OCI", "NT",
}

MODIFIER_SIGNAL = re.compile(r"(PWD|PH|HA|ORP|MINORITY|\d|-|\s)", re.I)

# ==========================================================
# STATE-SPECIFIC OVERRIDES
# ==========================================================
# Some raw codes CANNOT be classified by generic regex because the same
# short string means different things in different states (e.g. "SE" is
# Gujarat's SEBC/OBC, but a generic \bSE\b rule would misfire elsewhere;
# Karnataka's "GMPH" suffix means HK-region, not disability, while
# Gujarat's "PH" suffix always means disability). These were resolved via
# manual research against official state counselling documentation (DMER
# Maharashtra, KEA Karnataka, ACPUGMEC Gujarat, HP DME) - see notes.
#
# Keyed by (state, raw_category) exact match, checked BEFORE the generic
# RULES list. This is the permanent record of that research - if you
# delete master_category_mapping.csv and rerun from scratch, these still
# apply automatically.
#
# Format: (canonical_category, is_pwd, modifier, note)

STATE_OVERRIDES = {
    # --- Maharashtra: confirmed via official DMER/CET Cell seat-matrix legends ---
    ("Maharashtra", "HA"): ("OTHER", False, "Hilly-Area",
        "DMER legend: HA = Hilly Area reservation, not caste-based."),
    ("Maharashtra", "D1"): ("OTHER", False, "Defence-1",
        "DMER legend: D1/D2/D3 = Defence personnel ward categories, not disability."),
    ("Maharashtra", "D2"): ("OTHER", False, "Defence-2", "DMER legend: Defence category 2."),
    ("Maharashtra", "D3"): ("OTHER", False, "Defence-3", "DMER legend: Defence category 3."),
    ("Maharashtra", "D1HA"): ("OTHER", False, "Defence-1-Hilly-Area", "Combined Defence-1 + Hilly Area."),
    ("Maharashtra", "MKB"): ("OTHER", False, "MKB-Disputed-Border-Area",
        "MKB = Maharashtra-Karnataka disputed border area candidates."),
    ("Maharashtra", "ORP-A"): ("OTHER", False, "Orphan-Institutional",
        "DMER legend: ORP-A/ORP-B = Orphan, Institutional (IWC/IOG homes)."),
    ("Maharashtra", "ORP-C"): ("OTHER", False, "Orphan-Outside-Institution",
        "DMER legend: ORP-C = Orphan, Outside Institution (NIN)."),
    ("Maharashtra", "ORPHAN"): ("OTHER", False, "Orphan", "Generic orphan, same family as ORP-A/ORP-C."),
    ("Maharashtra", "ORPHANC"): ("OTHER", False, "Orphan-Outside-Institution", "Same as ORP-C, alt spelling."),
    ("Maharashtra", "PWD"): ("GENERAL", True, "",
        "Standalone PWD with no caste prefix = disability flag on Open/General seats."),
    ("Maharashtra", "I.Q."): ("MANAGEMENT", False, "Institute-Quota",
        "DMER legend: I.Q. = Institute Quota (private college's own quota)."),
    ("Maharashtra", "NT"): ("NT", False, "",
        "Generic NT bucket in fees file (vs split NTB/NTC/NTD in cutoff file)."),
    ("Maharashtra", "SEB"): ("OBC", False, "SEBC", "Abbreviated SEBC in fees file."),

    # --- Karnataka: confirmed via KEA counselling documentation ---
    ("Karnataka", "GMP"): ("GENERAL", False, "Govt-Merit-Private-Karnataka-Domicile",
        "GMP = General Merit Private, merit-based (not paid) seats for Karnataka domicile candidates."),
    ("Karnataka", "GMPH"): ("GENERAL", True, "Govt-Merit-Private-HK-Region",
        "GMP seats specific to Hyderabad-Karnataka (HK) region colleges. NOTE: PH suffix here means HK region, NOT disability - do not reuse this override's is_pwd elsewhere."),
    ("Karnataka", "MA"): ("MINORITY", False, "Minority-Arabic", "MA = Minority Arabic quota."),
    ("Karnataka", "ME"): ("MINORITY", False, "Minority-English-Christian", "ME = Minority English (Christian-medium)."),
    ("Karnataka", "MEH"): ("MINORITY", False, "Minority-English-HK-Region", "ME seats specific to HK region."),
    ("Karnataka", "MM"): ("MINORITY", False, "Minority-Muslim", "MM = Minority Muslim."),
    ("Karnataka", "MMH"): ("MINORITY", False, "Minority-Muslim-HK-Region", "MM seats specific to HK region."),
    ("Karnataka", "MU"): ("MINORITY", False, "Minority-Urdu", "MU = Minority Urdu quota."),
    ("Karnataka", "OTH"): ("OTHER", False, "Miscellaneous",
        "OTH = Others, a genuine miscellaneous KEA bucket with no single caste/category meaning."),
    ("Karnataka", "RC1"): ("MINORITY", False, "Religious-Community-1", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC2"): ("MINORITY", False, "Religious-Community-2", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC3"): ("MINORITY", False, "Religious-Community-3", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC4"): ("MINORITY", False, "Religious-Community-4", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC5"): ("MINORITY", False, "Religious-Community-5", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC6"): ("MINORITY", False, "Religious-Community-6", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC7"): ("MINORITY", False, "Religious-Community-7", "Religious Congregation seat, deemed university."),
    ("Karnataka", "RC8"): ("MINORITY", False, "Religious-Community-8", "Religious Congregation seat, deemed university."),

    # --- Gujarat: confirmed via ACPUGMEC / Gujarat SEBC documentation ---
    ("Gujarat", "SE"): ("OBC", False, "SEBC",
        "SE = SEBC (Socially and Educationally Backward Class), Gujarat's OBC equivalent, 27% reservation."),
    ("Gujarat", "SEPH"): ("OBC", True, "SEBC", "SEBC + disability suffix."),
    ("Gujarat", "EWPH"): ("EWS", True, "",
        "Gujarat consistently uses PH suffix = disability across category bases (unlike Karnataka's GMPH)."),
    ("Gujarat", "OPPH"): ("GENERAL", True, "", "Open + disability suffix."),
    ("Gujarat", "SCPH"): ("SC", True, "", "SC + disability suffix."),
    ("Gujarat", "STPH"): ("ST", True, "", "ST + disability suffix."),
    ("Gujarat", "MQ"): ("MANAGEMENT", False, "", "Management Quota."),

    # --- Himachal Pradesh: these are QUOTA types, not reservation categories ---
    ("Himachal Pradesh", "State Quota"): ("QUOTA_NOT_CATEGORY", False, "",
        "This is a quota/seat-type (85% domicile quota), not a caste/reservation category. "
        "Track in a separate quota_type field; excluded from frontend category export."),
    ("Himachal Pradesh", "Mgt Quota"): ("QUOTA_NOT_CATEGORY", False, "",
        "Management Quota is a quota/seat-type (paid private-college seats), not a category. "
        "Track in a separate quota_type field; excluded from frontend category export."),
    ("Himachal Pradesh", "IRDP/BPL"): ("OTHER", False, "Income-Based-BPL",
        "Income-based reservation (Below Poverty Line), orthogonal to caste categories."),
    ("Himachal Pradesh", "IRDP / BPL"): ("OTHER", False, "Income-Based-BPL", "Spacing variant of above."),

    # --- Bihar ---
    ("Bihar", "EBC"): ("OBC", False, "Extremely-Backward-Class",
        "EBC = Extremely Backward Class, a distinct sub-quota within Bihar's backward-class reservation."),
    ("Bihar", "General / Minority"): ("MINORITY", False, "Minority-run-college-general-quota",
        "Minority-run college's general-category seat allocation, not a student-facing caste category."),
    ("Bihar", "General / Muslim Minority"): ("MINORITY", False, "Muslim-Minority-college-quota",
        "Muslim-minority-run college's seat allocation."),
}


def auto_classify(raw_category: str, state: str = None):
    """
    Returns (canonical_category, is_pwd, modifier, confidence).
    Checks STATE_OVERRIDES first (exact, research-verified matches), then
    falls back to generic regex RULES, then to ('OTHER', ..., 'LOW') if
    nothing matches - OTHER-via-fallback rows always need human review,
    but OTHER-via-STATE_OVERRIDES (e.g. Karnataka OTH) do not, since
    that's a confirmed, deliberate classification, not an unknown.
    """
    norm = normalize(raw_category)

    if state is not None and (state, raw_category) in STATE_OVERRIDES:
        canonical, is_pwd, modifier, note = STATE_OVERRIDES[(state, raw_category)]
        return canonical, is_pwd, (modifier or None), "HIGH"

    is_pwd = bool(PWD_PATTERN.search(norm))

    for pattern, canonical, confidence in RULES:
        if pattern.search(norm):
            if norm.upper() in PLAIN_SYNONYMS or not MODIFIER_SIGNAL.search(norm):
                modifier = ""
            else:
                modifier = norm
            return canonical, is_pwd, modifier, confidence

    return "OTHER", is_pwd, norm, "LOW"


# ==========================================================
# EXTRACTION (reuses logic from your existing inventory script)
# ==========================================================

def extract_state(filename: str) -> str:
    name = filename.lower().replace(".csv", "")
    remove = ["_cutoff_data", "_cutoff_2024", "_cutoff_2025", "_cutoff",
              "_fees_data", "_fees", "_seat_data", "_seat", "_seats"]
    for r in remove:
        name = name.replace(r, "")
    return name.replace("_", " ").title()


def dataset_type(filename: str) -> str:
    name = filename.lower()
    if "cutoff" in name:
        return "Cutoff"
    if "seat" in name:
        return "Seats"
    if "fee" in name:
        return "Fees"
    return "Unknown"


def extract_all_raw_categories(data_folder: str) -> pd.DataFrame:
    """Scans every CSV in data_folder, returns a dataframe of unique
    (state, dataset, file, column, raw_category) rows found right now."""
    rows = []
    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv"):
            continue
        path = os.path.join(data_folder, file)
        try:
            df = pd.read_csv(path)
        except Exception as e:
            print(f"  [SKIP] Could not read {file}: {e}")
            continue

        cols_lower = {c.lower(): c for c in df.columns}
        state = extract_state(file)
        dtype = dataset_type(file)

        for col_key in CATEGORY_COLUMNS:
            if col_key not in cols_lower:
                continue
            col = cols_lower[col_key]
            values = df[col].dropna().astype(str).str.strip().unique()
            for v in sorted(values):
                if v == "":
                    continue
                rows.append({
                    "state": state,
                    "dataset": dtype,
                    "file": file,
                    "column": col,
                    "raw_category": v,
                })
    return pd.DataFrame(rows)


# ==========================================================
# MASTER MAPPING - LOAD / UPDATE
# ==========================================================

MASTER_COLUMNS = [
    "state", "raw_category", "canonical_category", "is_pwd", "modifier",
    "confidence", "status", "first_seen", "last_seen", "notes",
]


def load_master() -> pd.DataFrame:
    if os.path.exists(MASTER_MAPPING_FILE):
        return pd.read_csv(MASTER_MAPPING_FILE, dtype=str).fillna("")
    return pd.DataFrame(columns=MASTER_COLUMNS)


def update_master(master_df: pd.DataFrame, found_df: pd.DataFrame) -> pd.DataFrame:
    """
    Diff freshly-scanned raw categories against the master file.
    - Known (state, raw_category) pairs: just bump last_seen.
    - Unknown pairs: run through auto_classify, insert as needs_review.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if master_df.empty:
        known_keys = set()
    else:
        known_keys = set(zip(master_df["state"], master_df["raw_category"]))

    new_rows = []
    for _, r in found_df.drop_duplicates(subset=["state", "raw_category"]).iterrows():
        key = (r["state"], r["raw_category"])
        if key in known_keys:
            master_df.loc[
                (master_df["state"] == r["state"]) &
                (master_df["raw_category"] == r["raw_category"]),
                "last_seen"
            ] = today
            continue

        canonical, is_pwd, modifier, confidence = auto_classify(r["raw_category"], r["state"])
        new_rows.append({
            "state": r["state"],
            "raw_category": r["raw_category"],
            "canonical_category": canonical,
            "is_pwd": is_pwd,
            "modifier": modifier,
            "confidence": confidence,
            "status": "needs_review",
            "first_seen": today,
            "last_seen": today,
            "notes": "" if canonical != "OTHER" else "No rule matched - needs manual classification",
        })

    if new_rows:
        master_df = pd.concat([master_df, pd.DataFrame(new_rows)], ignore_index=True)

    return master_df


def approve_all_high_confidence(master_df: pd.DataFrame) -> pd.DataFrame:
    """
    Optional convenience: auto-approve rows the classifier is HIGH confidence
    about, so your team only manually reviews MEDIUM/LOW confidence rows.
    Comment this call out in main() if you'd rather review every new row by hand.
    """
    mask = (master_df["status"] == "needs_review") & (master_df["confidence"] == "HIGH")
    master_df.loc[mask, "status"] = "approved"
    return master_df


# ==========================================================
# OUTPUTS
# ==========================================================

def write_review_queue(master_df: pd.DataFrame):
    queue = master_df[master_df["status"] == "needs_review"].copy()
    queue = queue.sort_values(["confidence", "state", "raw_category"])
    queue.to_csv(REVIEW_QUEUE_FILE, index=False)
    print(f"  Review queue: {len(queue)} row(s) awaiting approval -> {REVIEW_QUEUE_FILE}")


def export_frontend_mapping(master_df: pd.DataFrame):
    """
    Clean, minimal export for the frontend team. Only approved rows.
    Structure: { "STATE": { "RAW_CATEGORY": {canonical, is_pwd, modifier} } }
    plus a flat "canonical_categories" list so frontend knows the fixed filter set.
    """
    approved = master_df[master_df["status"] == "approved"]

    mapping = {}
    for _, r in approved.iterrows():
        mapping.setdefault(r["state"], {})[r["raw_category"]] = {
            "canonical_category": r["canonical_category"],
            "is_pwd": str(r["is_pwd"]).upper() == "TRUE",
            "modifier": r["modifier"] if r["modifier"] else None,
        }

    output = {
        "canonical_categories": CANONICAL_CATEGORIES,
        "generated_at": datetime.now().isoformat(),
        "mapping": mapping,
    }

    with open(FRONTEND_EXPORT_FILE, "w", encoding="utf8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  Frontend export: {len(approved)} approved categories across "
          f"{len(mapping)} state(s) -> {FRONTEND_EXPORT_FILE}")


# ==========================================================
# MAIN
# ==========================================================

def main():
    print("=" * 60)
    print("CATEGORY MAPPING PIPELINE")
    print("=" * 60)

    print(f"\n[1/4] Scanning '{DATA_FOLDER}' for CSVs...")
    found_df = extract_all_raw_categories(DATA_FOLDER)
    print(f"  Found {len(found_df)} raw category occurrences "
          f"({found_df['raw_category'].nunique() if not found_df.empty else 0} unique strings)")

    print("\n[2/4] Loading master mapping and diffing...")
    master_df = load_master()
    before = len(master_df)
    master_df = update_master(master_df, found_df)
    added = len(master_df) - before
    print(f"  {added} new raw categor{'y' if added == 1 else 'ies'} added to master")

    # Comment out the next line if you want to hand-review every single new row,
    # including ones the classifier is confident about.
    master_df = approve_all_high_confidence(master_df)

    print("\n[3/4] Saving master mapping...")
    master_df.to_csv(MASTER_MAPPING_FILE, index=False)
    print(f"  -> {MASTER_MAPPING_FILE} ({len(master_df)} total rows)")

    print("\n[4/4] Writing review queue and frontend export...")
    write_review_queue(master_df)
    export_frontend_mapping(master_df)

    print("\nDone.")
    print("=" * 60)


if __name__ == "__main__":
    main()