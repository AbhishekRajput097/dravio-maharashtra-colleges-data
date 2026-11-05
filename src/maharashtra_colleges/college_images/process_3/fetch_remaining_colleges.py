"""
MBBS College Image Fetcher — Collegedunia + Direct URL approach
================================================================
Strategy:
  1. Use Collegedunia's search API (returns JSON with image URLs directly)
  2. Fallback to careers360.com direct college page
  3. Fallback to mbbscouncil.com direct college page

No browser needed for Collegedunia — pure requests (fast + reliable).
Browser used only for fallback sources.

INPUT:  playwright_failed_list.csv
OUTPUT: remaining_images_output.csv, remaining_success_list.csv, remaining_failed_list.csv
"""

import os, re, io, time, hashlib, random, json
import requests
import pandas as pd
from urllib.parse import urlparse, quote_plus
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# CONFIG
INPUT_CSV      = "../../../../data/maharashtra_colleges/college_images/process_2/playwright_failed_list.csv"
OUTPUT_CSV     = "../../../../data/maharashtra_colleges/college_images/process_3/remaining_images_output.csv"
SUCCESS_CSV    = "remaining_success_list.csv"
FAILED_OUT_CSV = "remaining_failed_list.csv"
IMAGES_DIR     = "college_images"
COL_NAME       = "college_name"
COL_STATE      = "state"
COL_TYPE       = "type"

DELAY_MIN      = 1.0
DELAY_MAX      = 2.0
PAGE_TIMEOUT   = 20000
RESTART_EVERY  = 80

MIN_WIDTH      = 300
MIN_HEIGHT     = 200
MIN_FILE_SIZE  = 8_000
MAX_ASPECT     = 5.0
MIN_ASPECT     = 0.5
VALID_EXTS     = {'.jpg', '.jpeg', '.png', '.webp', '.jfif'}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

URL_BLACKLIST = [
    'logo', 'icon', 'favicon', 'avatar', 'thumbnail',
    'banner', 'map', 'flag', 'seal', 'emblem',
    'chart', 'graph', 'infographic', 'watermark',
    'placeholder', 'default', 'no-image', 'noimage',
    'shareThumbnail', 'share-thumbnail',
    'news', 'blog', 'budget', 'fee', 'rank',
    'cutoff', 'score', 'result',
    'youtube', 'facebook', 'instagram', 'twitter',
    'stock', 'getty', 'shutterstock', 'istock',
    'slideserve', 'slideshare', 'powerpoint',
    'coin', 'money', 'graduation', 'certificate',
    'sprite', 'badge', 'stethoscope',
]

def slugify(name):
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80]

def short_name(name):
    return name.split(',')[0].strip()

def url_is_blacklisted(url):
    return any(kw in url.lower() for kw in URL_BLACKLIST)

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

def download_and_validate(url, referer="https://www.collegedunia.com/"):
    try:
        resp = requests.get(url, timeout=12,
            headers={"User-Agent": random.choice(USER_AGENTS),
                     "Referer": referer})
        if resp.status_code != 200:
            return False, None, f"HTTP {resp.status_code}"
        valid, reason = validate_image_bytes(resp.content)
        return valid, resp.content if valid else None, reason
    except Exception as e:
        return False, None, str(e)

def image_hash(b):
    return hashlib.md5(b).hexdigest()

def rdelay(a, b):
    time.sleep(random.uniform(a, b))

def get_headers(referer="https://www.collegedunia.com/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/html, */*",
        "Referer": referer,
    }

# ─────────────────────────────────────────────────────────────
# SOURCE 1: COLLEGEDUNIA API (no browser needed — pure requests)
# ─────────────────────────────────────────────────────────────

def search_collegedunia_api(college_name):
    """
    Collegedunia has a search API that returns JSON with college images.
    Much faster than browser — no JS rendering needed.
    """
    sname  = short_name(college_name)
    images = []

    try:
        # Step 1: Search API
        search_url = (
            f"https://collegedunia.com/api/college/search"
            f"?keyword={quote_plus(sname)}&stream=medicine&limit=5"
        )
        resp = requests.get(search_url, headers=get_headers(), timeout=10)

        if resp.status_code == 200:
            try:
                data = resp.json()
                colleges = data.get('data', data.get('colleges', data.get('results', [])))
                if isinstance(data, list):
                    colleges = data

                sname_words = set(re.findall(r'\b\w{4,}\b', sname.lower()))

                for college in colleges[:5]:
                    cname = str(college.get('name', '') or college.get('title', '')).lower()
                    cname_words = set(re.findall(r'\b\w{4,}\b', cname))
                    if len(sname_words & cname_words) >= min(2, len(sname_words)):
                        # Extract images from college data
                        for key in ['cover_image', 'image', 'logo', 'banner',
                                    'gallery', 'images', 'photos']:
                            val = college.get(key)
                            if isinstance(val, str) and val.startswith('http'):
                                if not url_is_blacklisted(val):
                                    images.append(val)
                            elif isinstance(val, list):
                                for item in val:
                                    url = item if isinstance(item, str) else item.get('url', '')
                                    if url and url.startswith('http') and not url_is_blacklisted(url):
                                        images.append(url)
                        if images:
                            break
            except Exception:
                pass

        # Step 2: Try direct college page via requests (no browser)
        if not images:
            slug = re.sub(r'[^a-z0-9\s]', '', sname.lower())
            slug = re.sub(r'\s+', '-', slug.strip())
            direct_urls = [
                f"https://collegedunia.com/mbbs/{slug}-college",
                f"https://collegedunia.com/college/{slug}",
            ]
            for durl in direct_urls:
                try:
                    resp2 = requests.get(durl, headers=get_headers(), timeout=10)
                    if resp2.status_code == 200:
                        content = resp2.text
                        # Collegedunia CDN patterns
                        for pattern in [
                            r'(https://[^\s"\'<>]+collegedunia[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
                            r'(https://d2epenzoyf672m\.cloudfront\.net/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
                            r'"coverImage":"(https?://[^"]+)"',
                            r'"image":"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"',
                        ]:
                            matches = re.findall(pattern, content, re.IGNORECASE)
                            for url in matches:
                                url = url.strip()
                                if not url_is_blacklisted(url):
                                    images.append(url)
                        if images:
                            break
                except Exception:
                    continue

    except Exception as e:
        print(f"         ⚠ Collegedunia: {e}")

    # Deduplicate
    seen, unique = set(), []
    for url in images:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:4]

# ─────────────────────────────────────────────────────────────
# SOURCE 2: CAREERS360 DIRECT API
# ─────────────────────────────────────────────────────────────

def search_careers360_api(college_name):
    """Careers360 search API — returns JSON with college data including images."""
    sname  = short_name(college_name)
    images = []

    try:
        # Careers360 search endpoint
        search_url = (
            f"https://medicine.careers360.com/api/colleges/search"
            f"?q={quote_plus(sname)}&limit=5"
        )
        resp = requests.get(search_url, headers=get_headers("https://medicine.careers360.com/"), timeout=10)

        if resp.status_code == 200:
            try:
                data = resp.json()
                colleges = data.get('data', data.get('results', []))
                if isinstance(data, list):
                    colleges = data

                sname_words = set(re.findall(r'\b\w{4,}\b', sname.lower()))
                for college in colleges[:5]:
                    cname = str(college.get('name', '') or college.get('title', '')).lower()
                    cname_words = set(re.findall(r'\b\w{4,}\b', cname))
                    if len(sname_words & cname_words) >= min(2, len(sname_words)):
                        for key in ['cover_image', 'image', 'thumbnail',
                                    'gallery_images', 'photos']:
                            val = college.get(key)
                            if isinstance(val, str) and val.startswith('http'):
                                if not url_is_blacklisted(val):
                                    images.append(val)
                            elif isinstance(val, list):
                                for item in val:
                                    url = item if isinstance(item, str) else item.get('url', '')
                                    if url and url.startswith('http') and not url_is_blacklisted(url):
                                        images.append(url)
                        if images:
                            break
            except Exception:
                pass

        # Fallback: direct page scrape
        if not images:
            slug = re.sub(r'[^a-z0-9\s]', '', sname.lower())
            slug = re.sub(r'\s+', '-', slug.strip())
            direct_url = f"https://medicine.careers360.com/colleges/{slug}"
            resp2 = requests.get(direct_url,
                headers=get_headers("https://medicine.careers360.com/"), timeout=10)
            if resp2.status_code == 200:
                content = resp2.text
                for pattern in [
                    r'(https://cache\.careers360\.mobi/media/colleges/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
                    r'"image":"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"',
                ]:
                    for url in re.findall(pattern, content, re.IGNORECASE):
                        url = url.strip()
                        if not url_is_blacklisted(url):
                            images.append(url)

    except Exception as e:
        print(f"         ⚠ Careers360: {e}")

    seen, unique = set(), []
    for url in images:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:4]

# ─────────────────────────────────────────────────────────────
# SOURCE 3: BROWSER-BASED FALLBACK (for sites that need JS)
# ─────────────────────────────────────────────────────────────

def search_with_browser(page, college_name, state):
    """
    Browser fallback for sites needing JS rendering.
    Tries collegedunia.com and careers360.com via browser.
    """
    sname  = short_name(college_name)
    images = []

    sites = [
        f"https://collegedunia.com/mbbs/colleges?search={quote_plus(sname)}",
        f"https://medicine.careers360.com/colleges/search?q={quote_plus(sname)}",
    ]

    CDN_PATTERNS = [
        r'(https://d2epenzoyf672m\.cloudfront\.net/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
        r'(https://cache\.careers360\.mobi/media/colleges/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
        r'(https://ik\.imagekit\.io/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
        r'(https://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp))',
    ]

    for site_url in sites:
        if len(images) >= 2:
            break
        try:
            page.goto(site_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            time.sleep(3)
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(1.5)

            content = page.content()

            sname_lower = sname.lower()
            sname_words = set(re.findall(r'\b\w{4,}\b', sname_lower))

            # Find college links matching our college
            links = page.locator("a").all()
            college_url = None
            for link in links[:20]:
                try:
                    href = link.get_attribute("href") or ""
                    text = (link.inner_text() or "").lower().strip()
                    text_words = set(re.findall(r'\b\w{4,}\b', text))
                    if (len(sname_words & text_words) >= min(2, len(sname_words))
                            and ('college' in href or 'mbbs' in href)):
                        college_url = href if href.startswith('http') else \
                            'https://' + site_url.split('/')[2] + href
                        break
                except Exception:
                    continue

            if college_url:
                page.goto(college_url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                time.sleep(3)
                page.evaluate("window.scrollBy(0, 800)")
                time.sleep(1.5)
                content = page.content()

            # Extract images from CDN patterns
            for pat in CDN_PATTERNS:
                for url in re.findall(pat, content, re.IGNORECASE):
                    url = url.split('"')[0].split("'")[0]
                    if not url_is_blacklisted(url):
                        images.append(url)
                if images:
                    break

        except Exception as e:
            print(f"         ⚠ Browser: {e}")
            continue
        rdelay(0.3, 0.5)

    seen, unique = set(), []
    for url in images:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:4]

# ─────────────────────────────────────────────────────────────
# MASTER FETCH
# ─────────────────────────────────────────────────────────────

def fetch_college_images(page, college_name, state):
    collected   = []
    seen_hashes = set()

    def try_urls(urls, referer="https://www.collegedunia.com/"):
        for img_url in urls:
            if len(collected) >= 2:
                break
            if url_is_blacklisted(img_url):
                continue
            valid, img_bytes, reason = download_and_validate(img_url, referer)
            if not valid:
                print(f"         ✗ {reason}")
                continue
            h = image_hash(img_bytes)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            collected.append((img_url, img_bytes))
            print(f"         ✓ {img_url[:70]}")

    # Source 1: Collegedunia API (fast, no browser)
    print(f"      📂 [Collegedunia API]")
    urls1 = search_collegedunia_api(college_name)
    print(f"         {len(urls1)} candidates")
    try_urls(urls1)
    rdelay(0.3, 0.5)

    # Source 2: Careers360 API (fast, no browser)
    if len(collected) < 2:
        print(f"      📂 [Careers360 API]")
        urls2 = search_careers360_api(college_name)
        print(f"         {len(urls2)} candidates")
        try_urls(urls2, "https://medicine.careers360.com/")
        rdelay(0.3, 0.5)

    # Source 3: Browser fallback
    if len(collected) < 2 and page:
        print(f"      📂 [Browser fallback]")
        urls3 = search_with_browser(page, college_name, state)
        print(f"         {len(urls3)} candidates")
        try_urls(urls3)

    return collected

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def create_browser(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=random.choice(USER_AGENTS),
        locale="en-US",
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    return browser, context, browser.new_page()

def main():
    if not os.path.exists(INPUT_CSV):
        print(f"❌ {INPUT_CSV} not found!")
        return

    df = pd.read_csv(INPUT_CSV)
    print(f"✓ Loaded {len(df)} colleges\n")

    os.makedirs(IMAGES_DIR, exist_ok=True)

    all_results, processed = [], set()
    if os.path.exists(OUTPUT_CSV):
        existing    = pd.read_csv(OUTPUT_CSV)
        all_results = existing.to_dict('records')
        processed   = set(existing[COL_NAME].tolist())
        print(f"✓ Resuming — {len(processed)}/{len(df)} done, next: #{len(processed)+1}\n")
    else:
        print(f"✓ Fresh run — starting from #1\n")

    start_time = time.time()

    with sync_playwright() as p:
        browser, context, page = create_browser(p)
        since_restart = 0
        done = 0

        for idx, row in df.iterrows():
            college_name = str(row[COL_NAME]).strip()
            state        = str(row.get(COL_STATE, '')).strip()
            college_type = str(row.get(COL_TYPE, '')).strip()

            if college_name in processed:
                continue

            if since_restart >= RESTART_EVERY:
                print(f"\n🔄 Restarting browser...")
                try: browser.close()
                except: pass
                rdelay(2, 3)
                browser, context, page = create_browser(p)
                since_restart = 0

            done += 1
            since_restart += 1
            elapsed  = time.time() - start_time
            avg_time = elapsed / done if done > 1 else 0
            rem      = len(df) - len(processed) - done
            eta_sec  = int(rem * avg_time)
            eta      = f"{eta_sec//60}m {eta_sec%60}s" if avg_time > 0 else "..."

            print(f"\n[{idx+1}/{len(df)}] {college_name[:60]}")
            print(f"   State: {state} | ETA: {eta}")

            img1_url = img2_url = ""
            status, notes = "failed", ""

            try:
                collected = fetch_college_images(page, college_name, state)

                if collected:
                    slug        = slugify(college_name)
                    college_dir = os.path.join(IMAGES_DIR, slug)
                    os.makedirs(college_dir, exist_ok=True)
                    saved = []
                    for i, (url, img_bytes) in enumerate(collected, 1):
                        ext = os.path.splitext(urlparse(url).path)[-1].lower()
                        if ext not in VALID_EXTS: ext = '.jpg'
                        save_path = os.path.join(college_dir, f"image_{i}{ext}")
                        with open(save_path, 'wb') as f:
                            f.write(img_bytes)
                        saved.append(url)
                    img1_url = saved[0] if len(saved) > 0 else ""
                    img2_url = saved[1] if len(saved) > 1 else ""
                    status   = "success_2" if len(saved) == 2 else "success_1"
                    notes    = f"{len(saved)} images"
                    print(f"      💾 {len(saved)} image(s) saved ✓")
                else:
                    notes = "Not found on any source"
                    print(f"      ✗ No images found")

            except Exception as e:
                notes = f"Error: {str(e)[:80]}"
                print(f"      ✗ Exception: {e}")

            all_results.append({
                "college_name": college_name, "state": state,
                "type": college_type, "status": status,
                "image_1_url": img1_url, "image_2_url": img2_url,
                "notes": notes,
            })
            pd.DataFrame(all_results).to_csv(OUTPUT_CSV, index=False)
            rdelay(DELAY_MIN, DELAY_MAX)

        try: browser.close()
        except: pass

    results_df = pd.DataFrame(all_results)

    s_df = results_df[results_df['status'].isin(['success_2','success_1'])].copy()
    s_df['images_count'] = s_df['status'].map({'success_2': 2, 'success_1': 1})
    s_df[['college_name','state','type','images_count',
          'image_1_url','image_2_url']].to_csv(SUCCESS_CSV, index=False)

    f_df = results_df[results_df['status'] == 'failed'].copy()
    f_df['manual_search_query'] = (
        '"' + f_df['college_name'].str.split(',').str[0].str.strip()
        + '" site:collegedunia.com OR site:careers360.com'
    )
    f_df[['college_name','state','type','notes',
          'manual_search_query']].to_csv(FAILED_OUT_CSV, index=False)

    elapsed = int(time.time() - start_time)
    s2 = len(results_df[results_df['status'] == 'success_2'])
    s1 = len(results_df[results_df['status'] == 'success_1'])
    fl = len(results_df[results_df['status'] == 'failed'])

    print(f"\n{'='*55}")
    print(f"✅ DONE in {elapsed//60}m {elapsed%60}s")
    print(f"  ✓ 2 images : {s2}")
    print(f"  ~ 1 image  : {s1}")
    print(f"  ✗ Failed   : {fl}")
    print(f"  Total      : {len(results_df)}")
    print(f"\n📄 {SUCCESS_CSV}  ({s2+s1} colleges)")
    print(f"📄 {FAILED_OUT_CSV}   ({fl} for manual)")

if __name__ == "__main__":
    main()