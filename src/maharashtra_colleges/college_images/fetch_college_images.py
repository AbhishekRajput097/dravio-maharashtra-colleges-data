"""
MBBS College Image Fetcher v3 — Improved with strict filtering + detailed tracking
====================================================================================
KEY IMPROVEMENTS over v2:
  1. Strict image relevance filtering (rejects banners, budget graphics, infographics,
     text-heavy images, fee/budget/rank graphics, news articles images)
  2. Image dimension validation (minimum size enforced)
  3. Keyword blacklist on both URL and page context
  4. Deduplication — no same image stored twice
  5. Detailed tracking CSV with 4 columns:
       - college_name, state, status, image_1_url, image_2_url, source, notes
  6. Separate summary report: success_list.csv + failed_list.csv

HOW TO RUN:
-----------
  pip install requests pandas beautifulsoup4 Pillow
  python fetch_college_images_v3.py

OUTPUT:
-------
  college_images_output.csv   → full tracking (all 823 colleges)
  success_list.csv            → only colleges WITH images found
  failed_list.csv             → only colleges WITHOUT images (for manual tracking)
  college_images/             → downloaded images
"""

import os, re, time, json, random, io, hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin, urlparse
from PIL import Image

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
CSV_FILE        = "../../../data/maharashtra_colleges/college_images/mbbs_college_list.csv"
VERSION         = "v3"   # Change this if you want a fresh run (e.g. "v4")
OUTPUT_CSV      = f"college_images_output_{VERSION}.csv"
SUCCESS_CSV     = f"success_list_{VERSION}.csv"
FAILED_CSV      = f"failed_list_{VERSION}.csv"
IMAGES_DIR      = "college_images"
DELAY           = 1.5
COL_NAME        = "Name and Address of Medical College / Medical Institution"
COL_STATE       = "State"
COL_TYPE        = "Type"

# Minimum image dimensions for a valid campus photo
MIN_WIDTH       = 400
MIN_HEIGHT      = 250
# Minimum file size (bytes) — rejects tiny/broken images
MIN_FILE_SIZE   = 15_000   # 15 KB
# Maximum aspect ratio (width/height) — rejects ultra-wide banners
MAX_ASPECT      = 4.5
# Minimum aspect ratio — rejects tall/portrait infographics
MIN_ASPECT      = 0.8

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

VALID_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.jfif'}

# ── URL-level blacklist keywords ──────────────────────────────
# If ANY of these appear in the image URL → reject immediately
URL_BLACKLIST = [
    'logo', 'icon', 'favicon', 'avatar', 'thumbnail', 'thumb',
    'banner', 'ad-', '-ad-', 'advertisement', 'sponsor',
    'map', 'flag', 'seal', 'emblem', 'badge', 'crest',
    'chart', 'graph', 'diagram', 'infographic', 'info-',
    'signature', 'watermark', 'pattern', 'sprite', 'bg-',
    'background', 'blank', 'no-image', 'noimage', 'placeholder',
    'default', 'user', 'profile', 'person', 'student', 'doctor',
    'news', 'article', 'blog', 'post', 'category', 'tag',
    'budget', 'fee', 'rank', 'seat', 'cut', 'score', 'result',
    'neet', 'mbbs-', 'counsell', 'admission-', 'exam-',
    'youtube', 'facebook', 'instagram', 'twitter', 'whatsapp',
    'qr-', 'qrcode', 'barcode',
    'arrow', 'calculator', 'coin', 'money', 'rupee',
    'graduation', 'degree', 'certificate', 'stethoscope',
]

# ── ALT-text / title blacklist ────────────────────────────────
# If img alt/title contains these → reject
ALT_BLACKLIST = [
    'budget', 'fee', 'rank', 'seat', 'cut off', 'cutoff', 'score',
    'result', 'neet', 'counselling', 'admission', 'exam',
    'logo', 'icon', 'banner', 'infographic', 'news', 'article',
    'blog', 'youtube', 'facebook', 'whatsapp', 'rating', 'review',
    'doctor', 'student', 'staff', 'faculty', 'person', 'people',
    '2024', '2025', '2026',   # year = likely news/banner image
    'mbbs budget', 'deemed medical', 'private medical college fee',
]

# ── Trusted image source domains ──────────────────────────────
# Images from these domains are more likely to be actual campus photos
TRUSTED_DOMAINS = [
    'rmgoe.org',
    'careermarg.com',
    'mbbscouncilcdn.s3',
    'cache.careers360.mobi',
    'edufever.com',
    'collegebatch.com',
    'pub-',           # Cloudflare R2
    'bookuradmission.com',
    'mdmsenquiry.com',
    'ugpgcollege.com',
    'vidyavision.com',
    'themedicalcounselling.com',
    'allegiance-educare.com',
]

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }

def slugify(name):
    name = name.lower()
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80]

def clean_name(name):
    parts = name.split(',')
    return ', '.join(parts[:2]).strip() if len(parts) > 1 else name.strip()

def url_is_blacklisted(url):
    url_lower = url.lower()
    for kw in URL_BLACKLIST:
        if kw in url_lower:
            return True, kw
    return False, None

def alt_is_blacklisted(alt_text):
    if not alt_text:
        return False
    alt_lower = alt_text.lower()
    for kw in ALT_BLACKLIST:
        if kw in alt_lower:
            return True
    return False

def is_from_trusted_domain(url):
    for domain in TRUSTED_DOMAINS:
        if domain in url:
            return True
    return False

def validate_image_bytes(image_bytes):
    """
    Validate image using PIL:
    - Check dimensions (min width/height)
    - Check aspect ratio (reject ultra-wide banners & tall infographics)
    - Check file size
    Returns (is_valid, reason)
    """
    if len(image_bytes) < MIN_FILE_SIZE:
        return False, f"too small ({len(image_bytes)} bytes)"
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < MIN_WIDTH:
            return False, f"width too small ({w}px)"
        if h < MIN_HEIGHT:
            return False, f"height too small ({h}px)"
        aspect = w / h
        if aspect > MAX_ASPECT:
            return False, f"too wide/banner-like (ratio {aspect:.1f})"
        if aspect < MIN_ASPECT:
            return False, f"too tall/portrait (ratio {aspect:.1f})"
        return True, f"OK ({w}x{h})"
    except Exception as e:
        return False, f"PIL error: {e}"

def fetch_and_validate_image(url, save_path=None):
    """
    Fetch image, validate dimensions, optionally save.
    Returns (success, image_bytes or None, reason)
    """
    try:
        resp = requests.get(url, headers=get_headers(), timeout=15, stream=False)
        if resp.status_code != 200:
            return False, None, f"HTTP {resp.status_code}"
        content_type = resp.headers.get('content-type', '')
        if 'image' not in content_type and not any(e in url.lower() for e in VALID_EXTS):
            return False, None, f"not an image ({content_type})"
        image_bytes = resp.content
        valid, reason = validate_image_bytes(image_bytes)
        if not valid:
            return False, None, reason
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(image_bytes)
        return True, image_bytes, reason
    except Exception as e:
        return False, None, str(e)

def image_hash(image_bytes):
    return hashlib.md5(image_bytes).hexdigest()

def extract_images_from_soup(soup, base_url, college_name):
    """
    Extract all candidate image URLs from a parsed page.
    Applies URL blacklist + alt text blacklist.
    Returns list of (url, score) tuples — higher score = more likely campus photo.
    """
    candidates = []
    college_words = set(re.findall(r'\b\w{4,}\b', college_name.lower()))

    for img in soup.find_all('img'):
        src = (img.get('src') or img.get('data-src') or
               img.get('data-lazy-src') or img.get('data-original') or '')
        src = src.strip()
        if not src:
            continue
        if not src.startswith('http'):
            src = urljoin(base_url, src)

        # Extension check
        path = urlparse(src).path.lower()
        ext = os.path.splitext(path)[-1]
        if ext and ext not in VALID_EXTS:
            continue

        # URL blacklist
        blocked, kw = url_is_blacklisted(src)
        if blocked:
            continue

        # Alt text blacklist
        alt = img.get('alt', '') or img.get('title', '') or ''
        if alt_is_blacklisted(alt):
            continue

        # Score the image
        score = 0
        if is_from_trusted_domain(src):
            score += 10
        if any(w in src.lower() for w in ['campus', 'building', 'college', 'hospital', 'medical']):
            score += 5
        # Check if alt text mentions the college name
        alt_words = set(re.findall(r'\b\w{4,}\b', alt.lower()))
        if college_words & alt_words:
            score += 3
        # Penalize clearly wrong images
        if any(w in src.lower() for w in ['stock', 'shutterstock', 'getty', 'istock']):
            score -= 5

        candidates.append((src, score))

    # Deduplicate by URL, keep highest score
    seen = {}
    for url, score in candidates:
        if url not in seen or seen[url] < score:
            seen[url] = score

    return sorted(seen.items(), key=lambda x: -x[1])

# ─────────────────────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────────────────────

def fetch_from_url(url, college_name):
    """Generic fetch: get page, extract + validate images."""
    images = []
    try:
        resp = requests.get(url, headers=get_headers(), timeout=10)
        if resp.status_code != 200:
            return images
        soup = BeautifulSoup(resp.text, 'html.parser')
        candidates = extract_images_from_soup(soup, url, college_name)
        for img_url, score in candidates:
            if score < 0:
                continue
            ok, img_bytes, reason = fetch_and_validate_image(img_url)
            if ok:
                images.append((img_url, img_bytes))
            if len(images) >= 2:
                break
    except Exception:
        pass
    return images

def search_rmgoe(name, state):
    slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    urls = [
        f"https://rmgoe.org/medical-colleges/{slug}",
        f"https://rmgoe.org/medical-colleges/{slug}-{state.lower().replace(' ','-')}",
    ]
    for url in urls:
        imgs = fetch_from_url(url, name)
        if imgs:
            return imgs
    # Try search page
    search = f"https://rmgoe.org/medical-colleges?search={quote_plus(name)}"
    return fetch_from_url(search, name)

def search_careermarg(name):
    slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    url = f"https://www.careermarg.com/college/{slug}"
    return fetch_from_url(url, name)

def search_mbbscouncil(name):
    slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    url = f"https://mbbscouncil.com/listing/{slug}/"
    return fetch_from_url(url, name)

def search_careers360(name):
    url = f"https://medicine.careers360.com/colleges/search?q={quote_plus(name)}&type=medical"
    imgs = fetch_from_url(url, name)
    if not imgs:
        # Try direct college page
        slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        url2 = f"https://medicine.careers360.com/colleges/{slug}"
        imgs = fetch_from_url(url2, name)
    return imgs

def search_collegebatch(name):
    slug = re.sub(r'[^a-z0-9\s]', ' ', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    url = f"https://www.collegebatch.com/colleges/{slug}-*"
    return fetch_from_url(url, name)

def search_edufever(name):
    slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    urls = [
        f"https://www.edufever.com/{slug}/",
        f"https://www.edufever.com/top-{slug}/",
    ]
    for url in urls:
        imgs = fetch_from_url(url, name)
        if imgs:
            return imgs
    return []

def search_mdmsenquiry(name):
    slug = re.sub(r'[^a-z0-9\s]', '', name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    url = f"https://mdmsenquiry.com/{slug}/"
    return fetch_from_url(url, name)

# ─────────────────────────────────────────────────────────────
# MASTER FETCH
# ─────────────────────────────────────────────────────────────

def fetch_images(college_name, state, college_type):
    clean = clean_name(college_name)
    sources = [
        ("rmgoe",        lambda: search_rmgoe(clean, state)),
        ("careermarg",   lambda: search_careermarg(clean)),
        ("mbbscouncil",  lambda: search_mbbscouncil(clean)),
        ("careers360",   lambda: search_careers360(clean)),
        ("collegebatch", lambda: search_collegebatch(clean)),
        ("edufever",     lambda: search_edufever(clean)),
        ("mdmsenquiry",  lambda: search_mdmsenquiry(clean)),
    ]

    collected = []       # list of (url, bytes)
    seen_hashes = set()
    sources_used = []

    for source_name, fn in sources:
        if len(collected) >= 2:
            break
        try:
            results = fn()
            for url, img_bytes in results:
                h = image_hash(img_bytes)
                if h in seen_hashes:
                    print(f"      [{source_name}] duplicate image skipped")
                    continue
                seen_hashes.add(h)
                collected.append((url, img_bytes))
                sources_used.append(source_name)
                print(f"      [{source_name}] ✓ {url[:80]}")
                if len(collected) >= 2:
                    break
        except Exception as e:
            pass
        time.sleep(0.3)

    return collected, sources_used

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    df = pd.read_csv(CSV_FILE)
    print(f"✓ Loaded {len(df)} colleges\n")

    os.makedirs(IMAGES_DIR, exist_ok=True)

    # ── Resume support ────────────────────────────────────────
    # IMPORTANT: Only resumes from THIS version's output file.
    # Old v2 file (college_images_output.csv) is completely ignored.
    all_results = []
    processed   = set()

    # Detect and warn about old version files
    old_files = [
        f for f in os.listdir('.')
        if re.match(r'college_images_output(_v\d+)?\.csv', f)
        and f != OUTPUT_CSV
    ]
    if old_files:
        print(f"⚠️  Old output file(s) detected and IGNORED: {old_files}")
        print(f"   This script ONLY resumes from: {OUTPUT_CSV}")
        print(f"   Delete {OUTPUT_CSV} if you want a completely fresh run.\n")

    if os.path.exists(OUTPUT_CSV):
        existing    = pd.read_csv(OUTPUT_CSV)
        all_results = existing.to_dict('records')
        processed   = set(existing['college_name'].tolist())
        print(f"✓ Resuming {VERSION} run — {len(processed)}/{len(df)} already done")
        print(f"  Next college: #{len(processed)+1}\n")
    else:
        print(f"✓ Fresh {VERSION} run — starting from college #1\n")

    for idx, row in df.iterrows():
        college_name = str(row[COL_NAME]).strip()
        state        = str(row[COL_STATE]).strip()
        college_type = str(row.get(COL_TYPE, '')).strip()

        if college_name in processed:
            continue

        print(f"[{idx+1}/{len(df)}] {college_name} | {state} | {college_type}")

        # Fetch with validation
        collected, sources_used = fetch_images(college_name, state, college_type)

        img1_url = img2_url = ""
        notes    = ""
        status   = "failed"

        if collected:
            slug = slugify(college_name)
            college_dir = os.path.join(IMAGES_DIR, slug)
            os.makedirs(college_dir, exist_ok=True)

            saved = []
            for i, (url, img_bytes) in enumerate(collected, 1):
                ext = os.path.splitext(urlparse(url).path)[-1] or '.jpg'
                if ext.lower() not in VALID_EXTS:
                    ext = '.jpg'
                save_path = os.path.join(college_dir, f"image_{i}{ext}")
                with open(save_path, 'wb') as f:
                    f.write(img_bytes)
                saved.append(url)
                print(f"      ✓ Saved image_{i}{ext}")

            img1_url = saved[0] if len(saved) > 0 else ""
            img2_url = saved[1] if len(saved) > 1 else ""
            notes    = f"Sources: {', '.join(sources_used)}"

            if len(saved) == 2:
                status = "success_2"
            elif len(saved) == 1:
                status = "success_1"
        else:
            notes = "No valid images found on any source"
            print(f"      ✗ No valid images found")

        all_results.append({
            "college_name": college_name,
            "state":        state,
            "type":         college_type,
            "status":       status,
            "image_1_url":  img1_url,
            "image_2_url":  img2_url,
            "sources_used": ', '.join(sources_used),
            "notes":        notes,
        })

        # Save progress after every college
        results_df = pd.DataFrame(all_results)
        results_df.to_csv(OUTPUT_CSV, index=False)

        time.sleep(DELAY)

    # ── Generate separate success + failed CSVs ──────────────
    results_df = pd.DataFrame(all_results)

    success_df = results_df[results_df['status'].isin(['success_2', 'success_1'])].copy()
    success_df['images_count'] = success_df['status'].map({'success_2': 2, 'success_1': 1})
    success_df = success_df[['college_name', 'state', 'type', 'images_count',
                              'image_1_url', 'image_2_url', 'sources_used']]
    success_df.to_csv(SUCCESS_CSV, index=False)

    failed_df = results_df[results_df['status'] == 'failed'].copy()
    failed_df = failed_df[['college_name', 'state', 'type', 'notes']]
    failed_df['manual_search_query'] = (
        '"' + failed_df['college_name'].str.split(',').str[0].str.strip() +
        '" campus building site:rmgoe.org OR site:careermarg.com OR site:edufever.com'
    )
    failed_df.to_csv(FAILED_CSV, index=False)

    # ── Final summary ─────────────────────────────────────────
    total        = len(results_df)
    success_2    = len(results_df[results_df['status'] == 'success_2'])
    success_1    = len(results_df[results_df['status'] == 'success_1'])
    failed_count = len(results_df[results_df['status'] == 'failed'])

    print(f"\n{'='*60}")
    print(f"✅ COMPLETED")
    print(f"{'='*60}")
    print(f"  ✓ 2 images found : {success_2} colleges")
    print(f"  ~ 1 image found  : {success_1} colleges")
    print(f"  ✗ No image found : {failed_count} colleges")
    print(f"  Total            : {total}")
    print(f"\n📄 Files saved:")
    print(f"  {OUTPUT_CSV}  → Full tracking (all {total} colleges)")
    print(f"  {SUCCESS_CSV}  → Colleges WITH images ({success_2+success_1})")
    print(f"  {FAILED_CSV}  → Colleges WITHOUT images ({failed_count}) — for manual tracking")
    print(f"\n💡 For failed colleges, use the 'manual_search_query' column in {FAILED_CSV}")
    print(f"   to quickly search Google/Brave for the right image.")

if __name__ == "__main__":
    main()