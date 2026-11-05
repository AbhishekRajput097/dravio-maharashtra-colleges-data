"""
College Image Cleaner — Final Production Version
=================================================
Features:
  - Reads MBBS_Colleges_Images_Links.csv
  - Skips known bad/duplicate URLs (Deoband, RNB Global, etc.)
  - Skips colleges with 0 images
  - Downloads + processes each image:
      → Smart crop bottom (removes road/vehicles)
      → Resize to 1200px longest side (NO stretching, natural ratio)
      → CLAHE + Unsharp mask + Contrast enhancement
  - Saves images as: "<College Name>_1.jpg" and "<College Name>_2.jpg"
  - Auto-resume if interrupted
  - Full tracking report

HOW TO RUN:
-----------
  pip install requests Pillow opencv-python numpy pandas
  python clean_college_images_final.py
"""

import os, re, io, random, time, requests
import numpy as np
import cv2
import pandas as pd
from PIL import Image, ImageEnhance
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
INPUT_CSV    = "../../../../data/maharashtra_colleges/college_images/image_cleaning/mbbs_colleges_images_links.csv"
OUTPUT_DIR   = "cleaned_images"
REPORT_CSV   = "../../../../data/maharashtra_colleges/college_images/image_cleaning/cleaning_report.csv"
MAX_SIDE     = 1200      # longest side in pixels
JPEG_QUALITY = 95

COL_NAME     = "college_name"
COL_IMG1     = "image_1_url"
COL_IMG2     = "image_2_url"
COL_STATE    = "state"
COL_COUNT    = "images_count"

# ── Known bad/wrong URLs — skip these completely ──────────────
BAD_URL_PATTERNS = [
    "deoband-unani-medical-college",
    "t-1720096940",
    "rnb-global-university",
    "t-1759306568",
    "encrypted-tbn0.gstatic.com",  # Google thumbnail — too small
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def safe_filename(name):
    """Convert college name to safe filename (keep readable, remove bad chars)."""
    name = name.strip()
    # Remove characters not safe for filenames
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '', name)
    # Replace multiple spaces with single space
    name = re.sub(r'\s+', ' ', name).strip()
    # Limit length
    return name[:120]

def is_bad_url(url):
    """Check if URL matches known bad/duplicate patterns."""
    if not url or pd.isna(url) or str(url).strip() in ('', 'nan'):
        return True
    url_str = str(url).lower()
    for pattern in BAD_URL_PATTERNS:
        if pattern.lower() in url_str:
            return True
    return False

def download_image(url):
    """Download image with multiple referer attempts. Returns PIL Image or None."""
    if is_bad_url(url):
        return None

    url = str(url).strip()
    if not url.startswith('http'):
        return None

    referers = [
        "https://www.careermarg.com/",
        "https://www.edufever.com/",
        "https://mbbscouncil.com/",
        "https://rmgoe.org/",
        "https://www.google.com/",
        "https://mdmsenquiry.com/",
        "https://www.collegebatch.com/",
    ]

    for referer in referers:
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Referer": referer,
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 5000:
                img = Image.open(io.BytesIO(resp.content)).convert('RGB')
                # Skip tiny images (thumbnails)
                if img.size[0] < 200 or img.size[1] < 150:
                    return None
                return img
        except Exception:
            continue
    return None

# ─────────────────────────────────────────────────────────────
# IMAGE PROCESSING PIPELINE
# ─────────────────────────────────────────────────────────────

def smart_crop_bottom(img_pil):
    """Remove road/vehicles/flat area from bottom of image."""
    arr  = np.array(img_pil)
    h, w = arr.shape[:2]
    gray = cv2.cvtColor(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2GRAY)

    bottom_crop = h
    for y in range(h - 1, int(h * 0.5), -1):
        if np.std(gray[y, :]) > 18:
            bottom_crop = y + 2
            break

    removed = h - bottom_crop
    if removed > 5:
        img_pil = img_pil.crop((0, 0, w, bottom_crop))

    return img_pil, removed

def resize_natural(img_pil, max_side=1200):
    """
    Resize keeping EXACT natural ratio. No stretching ever.
    Only scales DOWN if image is larger than max_side.
    Never upscales small images.
    """
    w, h  = img_pil.size
    # Scale so longest side = max_side, but never upscale
    scale = min(max_side / w, max_side / h, 1.0)
    if scale >= 1.0:
        return img_pil   # already small — don't upscale
    new_w = int(w * scale)
    new_h = int(h * scale)
    return img_pil.resize((new_w, new_h), Image.LANCZOS)

def enhance_image(img_pil):
    """
    Gentle enhancement — crisp without over-processing:
    - CLAHE clipLimit 1.5 (mild local contrast, not aggressive)
    - Single unsharp mask pass (not double)
    - Very mild final adjustments — no over-contrast
    """
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    # CLAHE on L channel only — mild setting
    lab     = cv2.cvtColor(img_cv, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l       = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(l)
    img_cv  = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    # Single unsharp mask pass (gentle)
    g      = cv2.GaussianBlur(img_cv, (0, 0), 1.0)
    img_cv = np.clip(cv2.addWeighted(img_cv, 1.4, g, -0.4, 0), 0, 255).astype(np.uint8)

    result = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
    result = ImageEnhance.Contrast(result).enhance(1.08)    # mild (was 1.35)
    result = ImageEnhance.Brightness(result).enhance(0.97)  # barely touch (was 0.93)
    result = ImageEnhance.Color(result).enhance(0.95)       # keep natural (was 0.88)
    result = ImageEnhance.Sharpness(result).enhance(1.2)    # gentle (was 1.5)
    return result

def process_image(img_pil):
    """Full pipeline: crop → resize → enhance."""
    img_pil, removed = smart_crop_bottom(img_pil)
    img_pil = resize_natural(img_pil, MAX_SIDE)
    img_pil = enhance_image(img_pil)
    return img_pil, removed

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(INPUT_CSV):
        print(f"❌ {INPUT_CSV} not found!")
        return

    df = pd.read_csv(INPUT_CSV)
    print(f"✓ Loaded {len(df)} total colleges")

    # Filter out colleges with 0 images
    df_with_images = df[df[COL_COUNT].fillna(0).astype(int) > 0].reset_index(drop=True)
    df_no_images   = df[df[COL_COUNT].fillna(0).astype(int) == 0].reset_index(drop=True)
    print(f"✓ Colleges with images   : {len(df_with_images)}")
    print(f"✓ Colleges without images: {len(df_no_images)}")
    print(f"✓ Output dir: {OUTPUT_DIR}/\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Resume support
    results   = []
    processed = set()
    if os.path.exists(REPORT_CSV):
        existing  = pd.read_csv(REPORT_CSV)
        results   = existing.to_dict('records')
        processed = set(existing[COL_NAME].tolist())
        print(f"✓ Resuming — {len(processed)} already done\n")

    start = time.time()

    for idx, row in df_with_images.iterrows():
        college_name = str(row[COL_NAME]).strip()
        state        = str(row.get(COL_STATE, '')).strip()
        img1_url     = str(row.get(COL_IMG1, '')).strip()
        img2_url     = str(row.get(COL_IMG2, '')).strip()

        if college_name in processed:
            continue

        done     = len([r for r in results if r['college_name'] not in processed]) + 1
        elapsed  = time.time() - start
        avg      = elapsed / max(done - 1, 1)
        rem      = (len(df_with_images) - len(processed) - done) * avg
        eta      = f"{int(rem)//60}m {int(rem)%60}s" if avg > 0 else "..."

        print(f"[{idx+1}/{len(df_with_images)}] {college_name[:60]} | ETA: {eta}")

        # Safe filename from college name
        fname_base = safe_filename(college_name)

        img1_clean = ""
        img2_clean = ""
        saved      = []

        for i, url in enumerate([img1_url, img2_url], 1):
            # Skip bad URLs
            if is_bad_url(url):
                print(f"   Image {i}: ✗ skipped (bad/duplicate URL)")
                continue

            img = download_image(url)
            if img is None:
                print(f"   Image {i}: ✗ download failed")
                continue

            orig_size = img.size
            try:
                processed_img, removed = process_image(img)
                # Save as "<College Name>_1.jpg"
                save_path = os.path.join(OUTPUT_DIR, f"{fname_base}_{i}.jpg")
                processed_img.save(save_path, "JPEG",
                                   quality=JPEG_QUALITY, optimize=True)
                size_kb = os.path.getsize(save_path) // 1024
                saved.append(save_path)
                print(f"   Image {i}: ✓ {orig_size} → {processed_img.size} "
                      f"(crop: {removed}px) [{size_kb}KB] → {fname_base}_{i}.jpg")
                if i == 1: img1_clean = save_path
                if i == 2: img2_clean = save_path
            except Exception as e:
                print(f"   Image {i}: ✗ failed: {e}")

        status = "success_2" if len(saved) == 2 else \
                 "success_1" if len(saved) == 1 else "failed"

        results.append({
            "college_name":     college_name,
            "state":            state,
            "status":           status,
            "image_1_url":      img1_url if not is_bad_url(img1_url) else "",
            "image_2_url":      img2_url if not is_bad_url(img2_url) else "",
            "cleaned_image_1":  img1_clean,
            "cleaned_image_2":  img2_clean,
            "saved_count":      len(saved),
        })

        pd.DataFrame(results).to_csv(REPORT_CSV, index=False)

    # Summary
    results_df = pd.DataFrame(results)
    s2  = len(results_df[results_df['status'] == 'success_2'])
    s1  = len(results_df[results_df['status'] == 'success_1'])
    fl  = len(results_df[results_df['status'] == 'failed'])

    elapsed = int(time.time() - start)
    print(f"\n{'='*60}")
    print(f"✅ DONE in {elapsed//60}m {elapsed%60}s")
    print(f"  ✓ 2 images cleaned   : {s2}")
    print(f"  ~ 1 image cleaned    : {s1}")
    print(f"  ✗ Failed/no image    : {fl}")
    print(f"  ✗ Skipped (0 images) : {len(df_no_images)}")
    print(f"  Total processed      : {len(results_df)}")
    print(f"\n📁 Cleaned images → {OUTPUT_DIR}/")
    print(f"   Filename format: <College Name>_1.jpg")
    print(f"📄 Report → {REPORT_CSV}")

if __name__ == "__main__":
    main()