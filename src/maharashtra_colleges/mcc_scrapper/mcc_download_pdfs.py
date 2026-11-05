"""
MCC PDF Downloader  v4  — FINAL
================================
From v3 log we now know:
  1. The iframe URL is: /Counseling/Handler/ViewInstituteProfileDetailsDynamic.ashx
                        ?boardId=140032521&InstituteId=200101
  2. It's a relative URL — just needs the domain prefix
  3. The pattern is predictable: InstituteId=CODE for every college
  4. We still need session cookies (the browser must be open to get valid ones)

So the approach is now dead simple:
  - Open browser, load page, get session cookies
  - For each college: GET https://mcc.admissions.nic.in/Counseling/Handler/
      ViewInstituteProfileDetailsDynamic.ashx?boardId=140032521&InstituteId=CODE
  - Save the PDF

No clicking, no popups, no iframes — just direct HTTP requests with cookies.

Usage:
    python mcc_download_pdfs_v4.py --visible
    python mcc_download_pdfs_v4.py
    python mcc_download_pdfs_v4.py --no-resume
"""

import re, time, logging
import requests, pandas as pd
from pathlib import Path
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from tqdm import tqdm


# ── Config ────────────────────────────────────────────────────────────────
BASE_DOMAIN  = "https://mcc.admissions.nic.in"
LIST_URL     = (BASE_DOMAIN + "/Counseling/Report/"
                "ViewInstituteProfileDynamic.aspx?agencyid=140&boardid=140032521")
# The actual PDF URL pattern discovered from v3 log
PDF_URL_TPL  = (BASE_DOMAIN + "/Counseling/Handler/"
                "ViewInstituteProfileDetailsDynamic.ashx"
                "?boardId=140032521&InstituteId={code}")

INST_TYPE_ID = "ctl00_ContentPlaceHolder1_ddlInstType"
PDF_DIR      = Path("pdfs")
CODES_CSV    = "../../../data/maharashtra_colleges/mcc_scrapper/mcc_codes_only.csv"
FAILED_CSV   = "failed_downloads.csv"
LOG_FILE     = "download.log"

DELAY        = 1.2    # seconds between requests
MAX_RETRIES  = 3
REQ_TIMEOUT  = 30
REFRESH_EVERY = 50    # refresh browser session cookies every N colleges


# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


# ── Driver ────────────────────────────────────────────────────────────────
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


# ── Load page and get session ─────────────────────────────────────────────
def load_page_and_get_session(driver) -> requests.Session:
    """Load the MCC list page (establishes session), return requests.Session with cookies."""
    log.info("Loading list page to establish session …")
    driver.get(LIST_URL)

    WebDriverWait(driver, 25).until(
        EC.presence_of_element_located((By.ID, INST_TYPE_ID))
    )

    # Wait for options then select All — this fully initialises the server session
    all_val = None
    for _ in range(30):
        opts = driver.execute_script("""
            var s=document.getElementById(arguments[0]);
            if(!s) return [];
            var r=[];
            for(var i=0;i<s.options.length;i++)
                r.push({v:s.options[i].value, t:s.options[i].text.trim()});
            return r;
        """, INST_TYPE_ID)
        nonempty = [o for o in opts if o["t"] and o["v"]]
        if nonempty:
            for o in nonempty:
                if o["t"].lower() == "all":
                    all_val = o["v"]; break
            if all_val is None: all_val = nonempty[0]["v"]
            break
        time.sleep(1)

    if all_val:
        driver.execute_script("""
            var s=document.getElementById(arguments[0]);
            s.value=arguments[1];
            s.dispatchEvent(new Event('change',{bubbles:true}));
        """, INST_TYPE_ID, all_val)
        log.info("Selected 'All'. Waiting for table …")
        time.sleep(5)

    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located(
                (By.XPATH, "//tbody/tr[td[2][string-length(normalize-space())>=6]]")
            )
        )
        log.info("Table ready.")
    except TimeoutException:
        log.warning("Table wait timed out — proceeding.")

    # Expand to all rows (also helps fully initialise session state)
    driver.execute_script("""
        var sels=document.querySelectorAll('select');
        for(var i=0;i<sels.length;i++){
            if(sels[i].name&&sels[i].name.indexOf('_length')!==-1){
                sels[i].value='-1';
                sels[i].dispatchEvent(new Event('change',{bubbles:true}));
                return;
            }
        }
    """)
    time.sleep(3)

    # Build requests session with browser cookies
    session = _make_session(driver)
    return session


def _make_session(driver) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer":    LIST_URL,
        "Accept":     "application/pdf,*/*",
    })
    cookies = driver.get_cookies()
    for c in cookies:
        s.cookies.set(c["name"], c["value"],
                      domain=c.get("domain",""), path=c.get("path","/"))
    log.info(f"Session cookies: {[c['name'] for c in cookies]}")
    return s


def collect_codes(driver) -> list[dict]:
    soup = BeautifulSoup(driver.page_source, "html.parser")
    institutes = []
    for t in soup.find_all("table"):
        tds = t.find_all("td")
        if not any(re.match(r"^\d{6}$", td.get_text(strip=True)) for td in tds[:40]):
            continue
        tbody = t.find("tbody") or t
        for row in tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3: continue
            code = cells[1].get_text(strip=True)
            name = cells[2].get_text(strip=True)
            if re.match(r"^\d{6}$", code):
                institutes.append({"institute_code": code, "institute_name": name})
        break
    seen, unique = set(), []
    for i in institutes:
        if i["institute_code"] not in seen:
            seen.add(i["institute_code"]); unique.append(i)
    log.info(f"Collected {len(unique)} codes.")
    pd.DataFrame(unique).to_csv(CODES_CSV, index=False)
    return unique


# ── Download one PDF ───────────────────────────────────────────────────────
def download_pdf(session: requests.Session, code: str) -> bytes | None:
    """Download PDF for a single institute code using the known URL pattern."""
    url = PDF_URL_TPL.format(code=code)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQ_TIMEOUT)
            content = resp.content
            ct = resp.headers.get("Content-Type", "")

            if resp.status_code == 200 and (
                "pdf" in ct.lower() or content[:4] == b"%PDF"
            ):
                log.info(f"  [{code}] ✓ {len(content):,} bytes  CT={ct}")
                return content

            # Log what we got instead
            log.warning(
                f"  [{code}] attempt {attempt}: "
                f"HTTP {resp.status_code}  CT={ct!r}  head={content[:16]!r}"
            )

            # If HTML — session may have expired, no point retrying immediately
            if content[:5] in (b"\r\n<!D", b"<!DOC", b"\xef\xbb\xbf"):
                log.warning(f"  [{code}] Got HTML — session may need refresh")
                return None   # caller will refresh and retry

        except requests.RequestException as e:
            log.warning(f"  [{code}] attempt {attempt}: request error: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(DELAY * attempt)

    return None


# ── Main ──────────────────────────────────────────────────────────────────
def main(resume=True, headless=True):
    PDF_DIR.mkdir(exist_ok=True)

    driver = get_driver(headless=headless)

    try:
        session    = load_page_and_get_session(driver)
        institutes = collect_codes(driver)

        if not institutes:
            log.error("No institutes found."); return

        if resume:
            pending = [i for i in institutes
                       if not (PDF_DIR / f"{i['institute_code']}.pdf").exists()]
        else:
            pending = institutes

        done_n = len(institutes) - len(pending)
        log.info(f"Total: {len(institutes)} | Done: {done_n} | To do: {len(pending)}")

        failed      = []
        html_streak = 0   # consecutive HTML responses = session expired

        for idx, inst in enumerate(tqdm(pending, desc="Downloading", unit="PDF")):
            code = inst["institute_code"]
            name = inst["institute_name"]

            # Refresh session cookies periodically
            if idx > 0 and idx % REFRESH_EVERY == 0:
                log.info(f"Refreshing session at college {idx} …")
                session = load_page_and_get_session(driver)
                html_streak = 0

            pdf = download_pdf(session, code)

            if pdf is None:
                html_streak += 1
                log.error(f"  FAILED [{code}] {name[:50]}")
                failed.append(inst)

                # If 3 HTML responses in a row, refresh session immediately
                if html_streak >= 3:
                    log.warning("Session likely expired — refreshing now …")
                    session = load_page_and_get_session(driver)
                    html_streak = 0
                    # Retry this college once with fresh session
                    pdf = download_pdf(session, code)
                    if pdf:
                        failed.pop()   # remove from failed list
                        (PDF_DIR / f"{code}.pdf").write_bytes(pdf)
                        log.info(f"  ✓ [{code}] saved after session refresh")
            else:
                html_streak = 0
                (PDF_DIR / f"{code}.pdf").write_bytes(pdf)

            time.sleep(DELAY)

        # Summary
        downloaded = sum(1 for i in institutes
                         if (PDF_DIR / f"{i['institute_code']}.pdf").exists())
        log.info(f"\n{'='*50}")
        log.info(f"Downloaded : {downloaded} / {len(institutes)}")
        log.info(f"Failed     : {len(failed)}")

        if failed:
            pd.DataFrame(failed).to_csv(FAILED_CSV, index=False)
            log.info(f"Failed list → {FAILED_CSV}  (re-run to retry)")

        print(f"\n✓ Done. {downloaded}/{len(institutes)} PDFs saved in '{PDF_DIR}/'")
        if failed:
            print(f"  {len(failed)} failed → {FAILED_CSV}  (just re-run to retry)")

    finally:
        driver.quit()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="MCC PDF Downloader v4")
    p.add_argument("--no-resume", action="store_true", help="Re-download everything")
    p.add_argument("--visible",   action="store_true", help="Show Chrome window")
    args = p.parse_args()
    main(resume=not args.no_resume, headless=not args.visible)