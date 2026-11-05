"""
MBBS College Image Fetcher — Playwright SPEED OPTIMIZED
========================================================
Same WORKING logic as the previous version.
Only the delays have been reduced to speed it up.

Changes from slow version:
  - DELAY_BETWEEN   : 2.0s → 0.8s
  - SCROLL_PAUSE    : 1.5s → 0.8s
  - Cookie wait     : 1.0s → 0.3s
  - Between queries : 0.5s → 0.2s
  - Click wait      : 0.8s → 0.4s
  - Page timeout    : 20000 → 15000ms
  - Max queries     : 4    → 3

Everything else (click logic, image extraction, validation) is IDENTICAL to working version.

HOW TO RUN:
-----------
  pip install playwright requests pandas Pillow
  playwright install chromium
  python fetch_failed_colleges_playwright.py
"""

import os, re, io, time, hashlib
import requests
import pandas as pd
from urllib.parse import urlparse, quote_plus
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
FAILED_CSV     = "../../../../data/maharashtra_colleges/college_images/failed_list_v3.csv"
OUTPUT_CSV     = "../../../../data/maharashtra_colleges/college_images/process_2/playwright_images_output.csv"
SUCCESS_CSV    = "../../../../data/maharashtra_colleges/college_images/process_2/playwright_success_list.csv"
FAILED_OUT_CSV = "../../../../data/maharashtra_colleges/college_images/process_2/playwright_failed_list.csv"
IMAGES_DIR     = "college_images"
COL_NAME       = "college_name"
COL_STATE      = "state"
COL_TYPE       = "type"

DELAY_BETWEEN  = 0.8    # ⚡ was 2.0
SCROLL_PAUSE   = 0.8    # ⚡ was 1.5
PAGE_TIMEOUT   = 15000  # ⚡ was 20000
MAX_IMAGES_SCAN = 12

# Image validation
MIN_WIDTH     = 400
MIN_HEIGHT    = 250
MIN_FILE_SIZE = 15_000
MAX_ASPECT    = 4.5
MIN_ASPECT    = 0.8
VALID_EXTS    = {'.jpg', '.jpeg', '.png', '.webp', '.jfif'}

URL_BLACKLIST = [
    'logo', 'icon', 'favicon', 'avatar', 'thumbnail', 'thumb',
    'banner', 'advertisement', 'map', 'flag', 'seal', 'emblem',
    'chart', 'graph', 'infographic', 'watermark', 'pattern',
    'background', 'placeholder', 'default', 'no-image',
    'news', 'article', 'blog', 'budget', 'fee', 'rank',
    'cutoff', 'score', 'result', 'counsell', 'admission-',
    'youtube', 'facebook', 'instagram', 'twitter', 'whatsapp',
    'qrcode', 'calculator', 'coin', 'money', 'graduation',
    'stock', 'getty', 'shutterstock', 'istock', 'dreamstime',
    'sprite', 'badge', 'certificate', 'stethoscope',
]

QUERY_TEMPLATES = [
    '"{name}" campus building',
    '"{name}" medical college campus',
    '{short_name} medical college {state} campus',
]

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def slugify(name):
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80]

def short_name(name):
    return name.split(',')[0].strip()

def url_is_blacklisted(url):
    url_lower = url.lower()
    return any(kw in url_lower for kw in URL_BLACKLIST)

def validate_image_bytes(image_bytes):
    if len(image_bytes) < MIN_FILE_SIZE:
        return False, f"too small ({len(image_bytes)}B)"
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < MIN_WIDTH:  return False, f"width {w}px"
        if h < MIN_HEIGHT: return False, f"height {h}px"
        ratio = w / h
        if ratio > MAX_ASPECT: return False, f"too wide ({ratio:.1f})"
        if ratio < MIN_ASPECT: return False, f"too tall ({ratio:.1f})"
        return True, f"{w}x{h}"
    except Exception as e:
        return False, str(e)

def download_and_validate(url):
    try:
        resp = requests.get(url, timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return False, None, f"HTTP {resp.status_code}"
        valid, reason = validate_image_bytes(resp.content)
        return valid, resp.content if valid else None, reason
    except Exception as e:
        return False, None, str(e)

def image_hash(b):
    return hashlib.md5(b).hexdigest()

# ─────────────────────────────────────────────────────────────
# GOOGLE IMAGES — SAME WORKING LOGIC, FASTER DELAYS
# ─────────────────────────────────────────────────────────────

def get_google_image_urls(page, query, max_results=12):
    """
    SAME logic as working version — click-based full image extraction.
    Only delays reduced.
    """
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=isch&hl=en"

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        time.sleep(SCROLL_PAUSE)

        # Accept cookies if popup appears
        try:
            for btn_text in ["Accept all", "I agree", "Accept"]:
                btn = page.locator(f"button:has-text('{btn_text}')")
                if btn.count() > 0:
                    btn.first.click()
                    time.sleep(0.3)  # ⚡ was 1.0
                    break
        except Exception:
            pass

        # Scroll to load images
        page.evaluate("window.scrollBy(0, 600)")
        time.sleep(SCROLL_PAUSE)

        image_urls = []

        # Method 1: Get URLs from image elements directly
        img_elements = page.locator("img.YQ4gaf, img.rg_i, div[data-id] img").all()
        for img in img_elements[:max_results * 2]:
            try:
                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                if src.startswith("http") and not src.startswith("data:"):
                    if is_real_image_url(src):
                        image_urls.append(src)
            except Exception:
                continue

        # Method 2: Click each result to get full-size URL (SAME as working version)
        if len(image_urls) < 2:
            results = page.locator("div.eA0Zlc, div[jsname='N9Xkfe'], div.isv-r").all()
            for result in results[:max_results]:
                try:
                    result.click()
                    time.sleep(0.4)  # ⚡ was 0.8
                    full_img = page.locator("img.sFlh5c, img.iPVvYb, div.p7sI2 img").first
                    if full_img.count() > 0:
                        src = full_img.get_attribute("src") or ""
                        if src.startswith("http") and is_real_image_url(src):
                            image_urls.append(src)
                    if len(image_urls) >= 2:
                        break
                except Exception:
                    continue

        return list(dict.fromkeys(image_urls))  # deduplicate

    except PlaywrightTimeout:
        print(f"      ✗ Timeout")
        return []
    except Exception as e:
        print(f"      ✗ Error: {e}")
        return []


def is_real_image_url(url):
    if any(ext in url.lower() for ext in VALID_EXTS):
        return True
    return False


def fetch_images_for_college(page, college_name, state):
    sname = short_name(college_name)

    queries = [
        f'"{sname}" campus building',
        f'"{sname}" medical college campus',
        f'{sname} medical college {state} campus',
    ]

    collected   = []
    seen_hashes = set()

    for query in queries:
        if len(collected) >= 2:
            break

        print(f"      🔍 {query[:65]}")
        image_urls = get_google_image_urls(page, query, MAX_IMAGES_SCAN)
        print(f"         {len(image_urls)} URLs found")

        for img_url in image_urls:
            if len(collected) >= 2:
                break
            if url_is_blacklisted(img_url):
                continue
            valid, img_bytes, reason = download_and_validate(img_url)
            if not valid:
                continue
            h = image_hash(img_bytes)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            collected.append((img_url, img_bytes))
            print(f"         ✓ {img_url[:65]}")

        time.sleep(0.2)  # ⚡ was 0.5

    return collected

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(FAILED_CSV):
        print(f"❌ {FAILED_CSV} not found!")
        return

    df = pd.read_csv(FAILED_CSV)
    print(f"✓ Loaded {len(df)} colleges")

    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Resume support
    all_results = []
    processed   = set()
    if os.path.exists(OUTPUT_CSV):
        existing    = pd.read_csv(OUTPUT_CSV)
        all_results = existing.to_dict('records')
        processed   = set(existing[COL_NAME].tolist())
        print(f"✓ Resuming — {len(processed)}/{len(df)} done, next: #{len(processed)+1}\n")
    else:
        print(f"✓ Fresh run — starting #1\n")

    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()

        done = 0
        for idx, row in df.iterrows():
            college_name = str(row[COL_NAME]).strip()
            state        = str(row.get(COL_STATE, '')).strip()
            college_type = str(row.get(COL_TYPE, '')).strip()

            if college_name in processed:
                continue

            done += 1
            elapsed  = time.time() - start_time
            avg_time = elapsed / done if done > 1 else 0
            rem_cols = len(df) - len(processed) - done
            eta_secs = int(rem_cols * avg_time)
            eta      = f"{eta_secs//60}m {eta_secs%60}s" if avg_time > 0 else "..."

            print(f"\n[{idx+1}/{len(df)}] {college_name[:55]} | ETA: {eta}")

            img1_url = img2_url = ""
            status   = "failed"
            notes    = ""

            try:
                collected = fetch_images_for_college(page, college_name, state)

                if collected:
                    slug        = slugify(college_name)
                    college_dir = os.path.join(IMAGES_DIR, slug)
                    os.makedirs(college_dir, exist_ok=True)

                    saved = []
                    for i, (url, img_bytes) in enumerate(collected, 1):
                        ext = os.path.splitext(urlparse(url).path)[-1].lower()
                        if ext not in VALID_EXTS:
                            ext = '.jpg'
                        save_path = os.path.join(college_dir, f"image_{i}{ext}")
                        with open(save_path, 'wb') as f:
                            f.write(img_bytes)
                        saved.append(url)

                    img1_url = saved[0] if len(saved) > 0 else ""
                    img2_url = saved[1] if len(saved) > 1 else ""
                    status   = "success_2" if len(saved) == 2 else "success_1"
                    notes    = f"{len(saved)} images saved"
                    print(f"      💾 {len(saved)} image(s) saved")
                else:
                    notes = "No valid images found"
                    print(f"      ✗ No images found")

            except Exception as e:
                notes = f"Error: {str(e)[:80]}"
                print(f"      ✗ Error: {e}")

            all_results.append({
                "college_name": college_name,
                "state":        state,
                "type":         college_type,
                "status":       status,
                "image_1_url":  img1_url,
                "image_2_url":  img2_url,
                "notes":        notes,
            })

            pd.DataFrame(all_results).to_csv(OUTPUT_CSV, index=False)
            time.sleep(DELAY_BETWEEN)

        browser.close()

    # Save success + failed CSVs
    results_df = pd.DataFrame(all_results)

    success_df = results_df[results_df['status'].isin(['success_2','success_1'])].copy()
    success_df['images_count'] = success_df['status'].map({'success_2': 2, 'success_1': 1})
    success_df[['college_name','state','type','images_count',
                'image_1_url','image_2_url']].to_csv(SUCCESS_CSV, index=False)

    failed_df = results_df[results_df['status'] == 'failed'].copy()
    failed_df['manual_search_query'] = (
        '"' + failed_df['college_name'].str.split(',').str[0].str.strip() + '" campus building'
    )
    failed_df[['college_name','state','type','notes',
               'manual_search_query']].to_csv(FAILED_OUT_CSV, index=False)

    elapsed = int(time.time() - start_time)
    total   = len(results_df)
    s2      = len(results_df[results_df['status'] == 'success_2'])
    s1      = len(results_df[results_df['status'] == 'success_1'])
    failed  = len(results_df[results_df['status'] == 'failed'])

    print(f"\n{'='*55}")
    print(f"✅ DONE in {elapsed//60}m {elapsed%60}s")
    print(f"  ✓ 2 images : {s2}")
    print(f"  ~ 1 image  : {s1}")
    print(f"  ✗ Failed   : {failed}")
    print(f"  Total      : {total}")
    print(f"\n📄 {OUTPUT_CSV}")
    print(f"📄 {SUCCESS_CSV}  ({s2+s1} colleges)")
    print(f"📄 {FAILED_OUT_CSV}   ({failed} colleges)")

if __name__ == "__main__":
    main()