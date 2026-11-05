"""
MCC Institute Profile Scraper  v7  — ASP.NET PostBack solution
===============================================================
ROOT CAUSE (confirmed from v6 log):
  The "View Profile" button uses ASP.NET __doPostBack:
    javascript:__doPostBack('ctl00$ContentPlaceHolder1$gvInstituteList$ctl02$btnViewProfile','')
  
  When this fires, the browser POSTs the entire ASP.NET form to the server.
  The server responds with the PDF directly (Content-Type: application/pdf).
  Selenium can't capture that HTTP response body.

THE FIX — Two-part approach:
  Part A) Selenium loads the page, selects All, waits for table.
          We extract from the page HTML:
            - __VIEWSTATE (hidden field)
            - __VIEWSTATEGENERATOR  
            - __EVENTVALIDATION
            - The exact __doPostBack event target for each row's button
          
  Part B) For each college, we POST the form directly using requests
          WITH the Selenium session cookies. The POST returns the PDF.
          This is exactly what the browser does when you click the button.

Requirements:
    pip install selenium webdriver-manager pdfplumber requests pandas tqdm beautifulsoup4

Usage:
    python mcc_scraper_v7.py --visible
    python mcc_scraper_v7.py
    python mcc_scraper_v7.py --no-resume
    python mcc_scraper_v7.py --test-pdf ViewInstituteProfileDetailsDynamic.pdf
"""

import re, io, time, logging
import requests, pdfplumber, pandas as pd
from tqdm import tqdm
from pathlib import Path
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
BASE_URL     = "https://mcc.admissions.nic.in/Counseling/Report/"
LIST_URL     = BASE_URL + "ViewInstituteProfileDynamic.aspx?agencyid=140&boardid=140032521"
INST_TYPE_ID = "ctl00_ContentPlaceHolder1_ddlInstType"

CODES_CSV  = "../../../data/maharashtra_colleges/mcc_scrapper/mcc_codes_only.csv"
OUTPUT_CSV = "../../../data/maharashtra_colleges/mcc_scrapper/mcc_institute_profiles.csv"
PDF_DIR    = Path("pdfs")
ERROR_LOG  = "errors.log"

DELAY       = 1.5
REQ_TIMEOUT = 45
WAIT_SEC    = 25


# ═══════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(ERROR_LOG, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  DRIVER
# ═══════════════════════════════════════════════════════════════════════════

def get_driver(headless=True):
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=opts
    )


def make_session(driver) -> requests.Session:
    """Build requests.Session with live browser cookies."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": LIST_URL,
        "Origin":  "https://mcc.admissions.nic.in",
    })
    for c in driver.get_cookies():
        s.cookies.set(c["name"], c["value"],
                      domain=c.get("domain",""), path=c.get("path","/"))
    log.info(f"  Session cookies: {list(s.cookies.keys())}")
    return s


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 1 — Load page, select All, scrape table → get codes + postback IDs
# ═══════════════════════════════════════════════════════════════════════════

def load_and_scrape(driver) -> tuple[list[dict], str]:
    """
    Load the MCC list page, select All, expand table, scrape all rows.
    Returns (institutes, page_html).
    institutes = [{institute_code, institute_name, event_target}, ...]
    event_target is the __doPostBack first argument for that row's button.
    """
    log.info(f"Loading {LIST_URL} …")
    driver.get(LIST_URL)

    # Wait for type dropdown
    WebDriverWait(driver, WAIT_SEC).until(
        EC.presence_of_element_located((By.ID, INST_TYPE_ID))
    )

    # Wait for options to populate
    all_val = None
    for _ in range(30):
        opts = driver.execute_script("""
            var s=document.getElementById(arguments[0]);
            if(!s)return[];
            var r=[];
            for(var i=0;i<s.options.length;i++)
                r.push({v:s.options[i].value,t:s.options[i].text.trim()});
            return r;
        """, INST_TYPE_ID)
        nonempty = [o for o in opts if o["t"] and o["v"]]
        if nonempty:
            log.info(f"Type options: {[o['t'] for o in nonempty]}")
            for o in nonempty:
                if o["t"].lower() == "all":
                    all_val = o["v"]; break
            if all_val is None: all_val = nonempty[0]["v"]
            break
        time.sleep(1)

    # Select All
    if all_val:
        driver.execute_script("""
            var s=document.getElementById(arguments[0]);
            s.value=arguments[1];
            s.dispatchEvent(new Event('change',{bubbles:true}));
        """, INST_TYPE_ID, all_val)
        log.info(f"Selected All (value={all_val!r}). Waiting for table …")
        time.sleep(5)

    # Wait for rows
    try:
        WebDriverWait(driver, WAIT_SEC).until(
            EC.presence_of_element_located(
                (By.XPATH,"//tbody/tr[td[2][string-length(normalize-space())>=6]]")
            )
        )
        log.info("Table rows ready.")
    except TimeoutException:
        log.warning("Table wait timed out.")

    # Expand to show all rows (DataTable show-all)
    driver.execute_script("""
        var sels=document.querySelectorAll('select');
        for(var i=0;i<sels.length;i++){
            if(sels[i].name&&sels[i].name.indexOf('_length')!==-1){
                sels[i].value='-1';
                sels[i].dispatchEvent(new Event('change',{bubbles:true}));
                return 'expanded';
            }
        }
        return 'not found';
    """)
    time.sleep(3)

    # Get full page source for parsing
    page_html = driver.page_source
    return page_html


def parse_table_html(page_html: str) -> list[dict]:
    """
    Parse the page HTML to extract for every row:
      - institute_code
      - institute_name
      - event_target: the full __doPostBack first argument from the View Profile button
                      e.g. 'ctl00$ContentPlaceHolder1$gvInstituteList$ctl02$btnViewProfile'
    """
    soup = BeautifulSoup(page_html, "html.parser")
    institutes = []

    # Find table containing institute data
    target_table = None
    for t in soup.find_all("table"):
        tds = t.find_all("td")
        if any(re.match(r"^\d{6}$", td.get_text(strip=True)) for td in tds[:40]):
            target_table = t
            break

    if not target_table:
        log.error("Could not find data table in page HTML!")
        # Log a snippet for diagnosis
        log.error(f"Page HTML snippet: {page_html[2000:4000]}")
        return []

    tbody = target_table.find("tbody") or target_table
    rows = tbody.find_all("tr")
    log.info(f"Found {len(rows)} rows in table.")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        code = cells[1].get_text(strip=True)
        name = cells[2].get_text(strip=True)
        if not re.match(r"^\d{6}$", code):
            continue

        # Extract event_target from the View Profile button in cells[3]
        event_target = ""
        profile_cell = cells[3]

        for tag in profile_cell.find_all(["a", "input", "button"]):
            # Check href for javascript:__doPostBack(...)
            href = tag.get("href", "")
            onclick = tag.get("onclick", "")

            for attr_val in [href, onclick]:
                # Match __doPostBack('TARGET','ARG') or __doPostBack("TARGET","ARG")
                m = re.search(
                    r"__doPostBack\s*\(\s*['\"]([^'\"]+)['\"]",
                    attr_val, re.I
                )
                if m:
                    event_target = m.group(1)
                    break

            if event_target:
                break

        institutes.append({
            "institute_code":  code,
            "institute_name":  name,
            "event_target":    event_target,
        })

    log.info(f"Parsed {len(institutes)} institutes. "
             f"With event_target: {sum(1 for i in institutes if i['event_target'])}")

    # Log first 3 for verification
    for inst in institutes[:3]:
        log.info(f"  [{inst['institute_code']}] target={inst['event_target']!r}")

    return institutes


def get_aspnet_hidden_fields(page_html: str) -> dict:
    """
    Extract ASP.NET hidden form fields needed for PostBack:
    __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION, etc.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    fields = {}
    for inp in soup.find_all("input", type="hidden"):
        name = inp.get("name", "")
        val  = inp.get("value", "")
        if name:
            fields[name] = val

    # Log which ones we found
    aspnet_keys = [k for k in fields if k.startswith("__")]
    log.info(f"ASP.NET hidden fields: {aspnet_keys}")
    log.info(f"  __VIEWSTATE length: {len(fields.get('__VIEWSTATE',''))}")
    log.info(f"  __EVENTVALIDATION length: {len(fields.get('__EVENTVALIDATION',''))}")
    return fields


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 2 — POST to get PDF for each college
# ═══════════════════════════════════════════════════════════════════════════

def download_pdf_via_postback(
    session: requests.Session,
    hidden_fields: dict,
    institute: dict,
    page_html: str,
) -> bytes | None:
    """
    Replicate the browser's form POST when View Profile is clicked.
    
    ASP.NET __doPostBack works by:
    1. Setting __EVENTTARGET to the button's event target
    2. Setting __EVENTARGUMENT to '' (empty)
    3. POSTing the entire form (including __VIEWSTATE etc.) to the page URL
    4. Server returns the PDF as the response body
    """
    code         = institute["institute_code"]
    event_target = institute.get("event_target", "")

    if not event_target:
        log.warning(f"  [{code}] No event_target found — cannot POST")
        return None

    # Build the POST data: all hidden fields + event info + any visible selects
    post_data = dict(hidden_fields)  # copy

    # Core PostBack fields
    post_data["__EVENTTARGET"]   = event_target
    post_data["__EVENTARGUMENT"] = ""
    post_data["__ASYNCPOST"]     = "false"

    # Include the type dropdown value (required for form state)
    post_data["ctl00$ContentPlaceHolder1$ddlInstType"] = "All"

    # Also include any other visible select/input values from the page
    soup = BeautifulSoup(page_html, "html.parser")
    for sel in soup.find_all("select"):
        name = sel.get("name","")
        if not name or name in post_data:
            continue
        # Get selected option value
        selected = sel.find("option", selected=True)
        if selected:
            post_data[name] = selected.get("value","")
        else:
            opts = sel.find_all("option")
            post_data[name] = opts[0].get("value","") if opts else ""

    log.info(f"  [{code}] POSTing with event_target={event_target!r}")

    # POST to the list page URL (ASP.NET posts back to itself)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer":      LIST_URL,
        "Origin":       "https://mcc.admissions.nic.in",
    }

    try:
        resp = session.post(
            LIST_URL,
            data=post_data,
            headers=headers,
            timeout=REQ_TIMEOUT,
            allow_redirects=True,
        )
        content = resp.content
        ct = resp.headers.get("Content-Type", "")

        log.info(f"  [{code}] POST response: HTTP {resp.status_code} "
                 f"CT={ct} size={len(content):,} head={content[:8]!r}")

        if resp.status_code == 200 and (
            ct.startswith("application/pdf") or content[:4] == b"%PDF"
        ):
            return content

        # Got HTML back — the VIEWSTATE may have been stale
        # Log a diagnostic snippet
        if content[:4] in (b"\xef\xbb\xbf", b"<htm", b"<!DO"):
            snippet = content[:300].decode("utf-8", errors="replace")
            log.warning(f"  [{code}] HTML response (first 300): {snippet}")

        return None

    except requests.RequestException as e:
        log.error(f"  [{code}] POST failed: {e}")
        return None


def refresh_page_state(driver) -> tuple[list[dict], dict, str]:
    """
    Reload the list page to get fresh __VIEWSTATE and __EVENTVALIDATION.
    Returns (institutes, hidden_fields, page_html).
    Called at start and whenever VIEWSTATE becomes stale.
    """
    log.info("Refreshing page state (getting fresh VIEWSTATE) …")
    page_html   = load_and_scrape(driver)
    institutes  = parse_table_html(page_html)
    hidden      = get_aspnet_hidden_fields(page_html)
    return institutes, hidden, page_html


# ═══════════════════════════════════════════════════════════════════════════
#  PDF PARSER
# ═══════════════════════════════════════════════════════════════════════════

def _tv(lines, i):
    if i >= len(lines): return ""
    line = lines[i].strip()
    m = re.search(r"(\d[\d,]*)\s*$", line)
    if m: return m.group(1).replace(",", "")
    m = re.search(r"\b([YN])\s*$", line)
    return m.group(1) if m else ""


def parse_pdf(pdf_bytes: bytes) -> dict:
    result = {k: "" for k in [
        "college_name","admission_fee_total_inr",
        "year1_fee_inr","year2_fee_inr","year3_fee_inr","year4_fee_inr","year5_fee_inr",
        "stipend_year1_inr","stipend_year2_inr","stipend_year3_inr",
        "hostel_male","hostel_female",
        "fee_deducted_reallocation_inr","fee_reimbursed_if_resign_inr",
        "fee_forfeited_round2_inr",
        "nri_year1_usd","nri_year2_usd","nri_year3_usd","nri_year4_usd","nri_year5_usd",
        "annual_fee_inr",
    ]}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            lines = []
            for page in pdf.pages:
                lines.extend((page.extract_text() or "").splitlines())
    except Exception as e:
        log.error(f"pdfplumber: {e}"); return result

    for ln in lines:
        if ln.strip().startswith("Name of the College:"):
            result["college_name"] = ln.split(":",1)[1].strip()
        elif "Annual Fees of Candidates(INR):" in ln:
            result["annual_fee_inr"] = ln.split(":",1)[1].strip()
        elif re.match(r"Hostel facility for Male students\s*:", ln):
            result["hostel_male"] = ln.split(":")[-1].strip()
        elif re.match(r"Hostel facility for Female students\s*:", ln):
            result["hostel_female"] = ln.split(":")[-1].strip()

    inr_s = ["admission_fee_total_inr",
             "year1_fee_inr","year2_fee_inr","year3_fee_inr","year4_fee_inr","year5_fee_inr"]
    nri_s = ["nri_year1_usd","nri_year2_usd","nri_year3_usd","nri_year4_usd","nri_year5_usd"]
    ic = nc = 0

    for i,ln in enumerate(lines):
        ln = ln.strip()
        if re.match(r"Amount to be [Pp]aid at the time of", ln):
            v = _tv(lines,i)
            look = " ".join(lines[j].strip() for j in range(i+1,min(i+4,len(lines)))).lower()
            if "nri" in look:
                if nc<len(nri_s): result[nri_s[nc]]=v; nc+=1
            else:
                if ic<len(inr_s): result[inr_s[ic]]=v; ic+=1
        elif re.match(r"Stipend Paid to the students I st Year", ln):
            result["stipend_year1_inr"]=_tv(lines,i)
        elif re.match(r"Stipend Paid to the students IInd Year", ln):
            result["stipend_year2_inr"]=_tv(lines,i)
        elif re.match(r"Stipend Paid to the students IIIrd Year", ln):
            result["stipend_year3_inr"]=_tv(lines,i)
        elif re.match(r"The Amount of Fee to be deducted on", ln):
            v=_tv(lines,i)
            if not v:
                for j in range(i+1,min(i+5,len(lines))):
                    if "Counseling (INR)" in lines[j]: v=_tv(lines,j); break
            result["fee_deducted_reallocation_inr"]=v
        elif re.match(r"The Amount of Fees To be reimbursed", ln):
            result["fee_reimbursed_if_resign_inr"]=_tv(lines,i)
        elif re.match(r"Amount to be forfeited in case of", ln):
            for j in range(i,min(i+6,len(lines))):
                if "Round of counselling period (INR)" in lines[j]:
                    result["fee_forfeited_round2_inr"]=_tv(lines,j); break
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  CSV
# ═══════════════════════════════════════════════════════════════════════════

COLUMNS = [
    "institute_code","college_name","admission_fee_total_inr",
    "year1_fee_inr","year2_fee_inr","year3_fee_inr","year4_fee_inr","year5_fee_inr",
    "stipend_year1_inr","stipend_year2_inr","stipend_year3_inr",
    "hostel_male","hostel_female",
    "fee_deducted_reallocation_inr","fee_reimbursed_if_resign_inr","fee_forfeited_round2_inr",
    "nri_year1_usd","nri_year2_usd","nri_year3_usd","nri_year4_usd","nri_year5_usd",
    "annual_fee_inr",
]

def flush_csv(rows, path):
    df = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in df.columns: df[c]=""
    df[COLUMNS].to_csv(path, mode="a", index=False,
                       header=not Path(path).exists(), encoding="utf-8-sig")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def scrape_all(resume=True, headless=True):
    PDF_DIR.mkdir(exist_ok=True)

    done: set[str] = set()
    if resume and Path(OUTPUT_CSV).exists():
        done = set(pd.read_csv(OUTPUT_CSV, dtype=str)["institute_code"].dropna())
        log.info(f"Resume: {len(done)} done already.")

    log.info("Opening browser …")
    driver = get_driver(headless=headless)

    try:
        # Load page and get everything we need
        institutes, hidden_fields, page_html = refresh_page_state(driver)

        if not institutes:
            log.error("No institutes found. Aborting.")
            return

        # Save codes
        pd.DataFrame([{"institute_code":i["institute_code"],
                        "institute_name":i["institute_name"]}
                       for i in institutes]).to_csv(CODES_CSV, index=False)

        # Build requests session with browser cookies
        session = make_session(driver)

        pending = [i for i in institutes if i["institute_code"] not in done]
        log.info(f"Colleges to process: {len(pending)}")

        # Track consecutive failures — if too many, refresh VIEWSTATE
        consecutive_failures = 0
        REFRESH_AFTER = 20   # refresh page state every 20 colleges

        buf, failed = [], []

        for idx, inst in enumerate(tqdm(pending, desc="Colleges", unit="college")):
            code = inst["institute_code"]
            name = inst["institute_name"]

            # Periodically refresh VIEWSTATE to keep it valid
            if idx > 0 and idx % REFRESH_AFTER == 0:
                log.info(f"Refreshing page state at college {idx} …")
                institutes_new, hidden_fields, page_html = refresh_page_state(driver)
                # Merge event_targets from new parse into our working list
                new_targets = {i["institute_code"]: i["event_target"]
                               for i in institutes_new}
                for i in pending:
                    if i["institute_code"] in new_targets:
                        i["event_target"] = new_targets[i["institute_code"]]
                session = make_session(driver)
                consecutive_failures = 0

            # Update hidden fields freshly for this request
            inst["event_target"] = next(
                (i["event_target"] for i in institutes
                 if i["institute_code"] == code),
                inst.get("event_target","")
            )

            pdf_bytes = download_pdf_via_postback(session, hidden_fields, inst, page_html)

            if pdf_bytes is None:
                consecutive_failures += 1
                log.error(f"FAILED [{code}] {name[:50]} (consecutive: {consecutive_failures})")
                failed.append({"institute_code":code,"institute_name":name,
                                "event_target":inst.get("event_target","")})

                # If 3+ consecutive failures, refresh page state immediately
                if consecutive_failures >= 3:
                    log.warning("3 consecutive failures — refreshing page state now …")
                    institutes_new, hidden_fields, page_html = refresh_page_state(driver)
                    new_targets = {i["institute_code"]:i["event_target"]
                                   for i in institutes_new}
                    for i in pending:
                        if i["institute_code"] in new_targets:
                            i["event_target"] = new_targets[i["institute_code"]]
                    session = make_session(driver)
                    consecutive_failures = 0

                time.sleep(DELAY)
                continue

            consecutive_failures = 0
            (PDF_DIR / f"{code}.pdf").write_bytes(pdf_bytes)

            fields = parse_pdf(pdf_bytes)
            fields["institute_code"] = code
            if not fields["college_name"] or len(fields["college_name"]) < len(name)//2:
                fields["college_name"] = name
            buf.append(fields)

            if len(buf) >= 10:
                flush_csv(buf, OUTPUT_CSV)
                done.update(r["institute_code"] for r in buf)
                buf = []

            time.sleep(DELAY)

        if buf: flush_csv(buf, OUTPUT_CSV)
        if failed:
            pd.DataFrame(failed).to_csv("failed_institutes.csv", index=False)
            log.warning(f"{len(failed)} failed → failed_institutes.csv")

    finally:
        driver.quit()

    total = pd.read_csv(OUTPUT_CSV).shape[0] if Path(OUTPUT_CSV).exists() else 0
    log.info(f"✓ Done. {total} rows → {OUTPUT_CSV}")


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--visible",   action="store_true")
    p.add_argument("--test-pdf",  type=str, default=None)
    args = p.parse_args()

    if args.test_pdf:
        with open(args.test_pdf,"rb") as f: r = parse_pdf(f.read())
        for k,v in r.items(): print(f"  {k}: {v!r}")
    else:
        scrape_all(resume=not args.no_resume, headless=not args.visible)