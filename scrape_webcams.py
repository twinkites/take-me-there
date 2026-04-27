#!/usr/bin/env python3
"""
Scrape webcams from NPS (National Park Service) and SpotCameras and expand webcams.json.

Usage:
    python3 scrape_webcams.py                      # fetch all NPS webcams
    python3 scrape_webcams.py --source spotcameras  # fetch NY cams from spotcameras.com
    python3 scrape_webcams.py --dry-run             # print results without writing
    python3 scrape_webcams.py --limit N             # process only first N cams

PLEASE USE RESPONSIBLY
-----------------------
This script queries public websites. Please:
  - Run it infrequently (updates are rarely needed more than once a month).
  - Do not remove or reduce the per-request delay (time.sleep below).
  - Do not run multiple instances in parallel.
  - Use --limit during development to avoid unnecessary load.
  - Do not redistribute or commercialize data obtained from these sources.
NPS webcam images remain the property of the National Park Service:
  https://www.nps.gov/aboutus/disclaimer.htm
SpotCameras content is subject to spotcameras.com terms of use.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

# ── NPS park name → US state ───────────────────────────────────────────────

# Keyed on lowercase fragments found in the "Parks" Solr field
NAME_STATE = {
    "acadia":                "ME",
    "arches":                "UT",
    "assateague":            "MD",
    "badlands":              "SD",
    "big bend":              "TX",
    "blue ridge":            "VA",
    "boston":                "MA",
    "bryce canyon":          "UT",
    "cape cod":              "MA",
    "cape hatteras":         "NC",
    "canyonlands":           "UT",
    "capitol reef":          "UT",
    "carlsbad":              "NM",
    "channel islands":       "CA",
    "crater lake":           "OR",
    "craters of the moon":   "ID",
    "cuyahoga":              "OH",
    "death valley":          "CA",
    "denali":                "AK",
    "everglades":            "FL",
    "gates of the arctic":   "AK",
    "glacier bay":           "AK",
    "glacier national":      "MT",
    "grand canyon":          "AZ",
    "grand sand dunes":      "CO",
    "great basin":           "NV",
    "great sand dunes":      "CO",
    "great smoky":           "TN",
    "grand teton":           "WY",
    "guadalupe":             "TX",
    "hawaii volcanoes":      "HI",
    "independence":          "PA",
    "indiana dunes":         "IN",
    "isle royale":           "MI",
    "joshua tree":           "CA",
    "katmai":                "AK",
    "kenai fjords":          "AK",
    "lassen":                "CA",
    "mammoth cave":          "KY",
    "mesa verde":            "CO",
    "mojave":                "CA",
    "mount rainier":         "WA",
    "mount rushmore":        "SD",
    "new river gorge":       "WV",
    "olympic":               "WA",
    "petrified forest":      "AZ",
    "redwood":               "CA",
    "rocky mountain":        "CO",
    "saguaro":               "AZ",
    "sequoia":               "CA",
    "shenandoah":            "VA",
    "sleeping bear":         "MI",
    "theodore roosevelt":    "ND",
    "valles caldera":        "NM",
    "voyageurs":             "MN",
    "white sands":           "NM",
    "wind cave":             "SD",
    "yellowstone":           "WY",
    "yosemite":              "CA",
    "zion":                  "UT",
    # Additional parks
    "amistad":               "TX",
    "bandelier":             "NM",
    "black canyon":          "CO",
    "buffalo national":      "AR",
    "canaveral":             "FL",
    "canyon de chelly":      "AZ",
    "cape lookout":          "NC",
    "colonial":              "VA",
    "congaree":              "SC",
    "curecanti":             "CO",
    "de soto":               "FL",
    "dinosaur":              "CO",
    "fire island":           "NY",
    "fort jefferson":        "FL",
    "fort sumter":           "SC",
    "gettysburg":            "PA",
    "glen canyon":           "AZ",
    "golden gate":           "CA",
    "hot springs":           "AR",
    "hubbard":               "AK",
    "ice age":               "WI",
    "jean lafitte":          "LA",
    "jewel cave":            "SD",
    "lake clark":            "AK",
    "lake mead":             "NV",
    "lava beds":             "CA",
    "lowell":                "MA",
    "minuteman":             "MA",
    "missi":                 "MS",
    "natchez trace":         "MS",
    "natural bridges":       "UT",
    "niagara falls":         "NY",
    "north cascades":        "WA",
    "ozark":                 "MO",
    "padre island":          "TX",
    "pea ridge":             "AR",
    "pictured rocks":        "MI",
    "pinnacles":             "CA",
    "point reyes":           "CA",
    "rainbow bridge":        "UT",
    "ross lake":             "WA",
    "saguaro":               "AZ",
    "san antonio":           "TX",
    "sand creek":            "CO",
    "santa monica":          "CA",
    "scotts bluff":          "NE",
    "shenandoah":            "VA",
    "st. croix":             "WI",
    "statue of liberty":     "NY",
    "stonewall":             "NY",
    "upper delaware":        "PA",
    "vicksburg":             "MS",
    "virgin islands":        "VI",
    "wrangell":              "AK",
    "wright brothers":       "NC",
    "wupatki":               "AZ",
    "yellowstone":           "WY",
}

NPS_BASE    = "https://www.nps.gov"
SOLR_URL    = "https://www.nps.gov/solr/"
SOLR_FIELDS = (
    "Type,Title,Parks,PageURL,Image_URL,Abstract"
)

# ── SpotCameras config ─────────────────────────────────────────────────────
SPOTCAMERAS_BASE = "https://spotcameras.com"

# Mapping of US state/territory abbreviation → SpotCameras listing URL path
SPOTCAMERAS_STATES = {
    "AL": "/en/cams/United-States/Alabama",
    "AK": "/en/cams/United-States/Alaska",
    "AZ": "/en/cams/United-States/Arizona",
    "AR": "/en/cams/United-States/Arkansas",
    "CA": "/en/cams/United-States/California",
    "CO": "/en/cams/United-States/Colorado",
    "CT": "/en/cams/United-States/Connecticut",
    "DE": "/en/cams/United-States/Delaware",
    "DC": "/en/cams/United-States/District-of-Columbia",
    "FL": "/en/cams/United-States/Florida",
    "GA": "/en/cams/United-States/Georgia",
    "HI": "/en/cams/United-States/Hawaii",
    "ID": "/en/cams/United-States/Idaho",
    "IL": "/en/cams/United-States/Illinois",
    "IN": "/en/cams/United-States/Indiana",
    "IA": "/en/cams/United-States/Iowa",
    "KS": "/en/cams/United-States/Kansas",
    "KY": "/en/cams/United-States/Kentucky",
    "LA": "/en/cams/United-States/Louisiana",
    "ME": "/en/cams/United-States/Maine",
    "MD": "/en/cams/United-States/Maryland",
    "MA": "/en/cams/United-States/Massachusetts",
    "MI": "/en/cams/United-States/Michigan",
    "MN": "/en/cams/United-States/Minnesota",
    "MS": "/en/cams/United-States/Mississippi",
    "MO": "/en/cams/United-States/Missouri",
    "MT": "/en/cams/United-States/Montana",
    "NE": "/en/cams/United-States/Nebraska",
    "NV": "/en/cams/United-States/Nevada",
    "NH": "/en/cams/United-States/New-Hampshire",
    "NJ": "/en/cams/United-States/New-Jersey",
    "NM": "/en/cams/United-States/New-Mexico",
    "NY": "/en/cams/United-States/New-York",
    "NC": "/en/cams/United-States/North-Carolina",
    "ND": "/en/cams/United-States/North-Dakota",
    "OH": "/en/cams/United-States/Ohio",
    "OK": "/en/cams/United-States/Oklahoma",
    "OR": "/en/cams/United-States/Oregon",
    "PA": "/en/cams/United-States/Pennsylvania",
    "RI": "/en/cams/United-States/Rhode-Island",
    "SC": "/en/cams/United-States/South-Carolina",
    "SD": "/en/cams/United-States/South-Dakota",
    "TN": "/en/cams/United-States/Tennessee",
    "TX": "/en/cams/United-States/Texas",
    "UT": "/en/cams/United-States/Utah",
    "VT": "/en/cams/United-States/Vermont",
    "VA": "/en/cams/United-States/Virginia",
    "WA": "/en/cams/United-States/Washington",
    "WV": "/en/cams/United-States/West-Virginia",
    "WI": "/en/cams/United-States/Wisconsin",
    "WY": "/en/cams/United-States/Wyoming",
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)

# ── Solr fetch ─────────────────────────────────────────────────────────────

def fetch_all_webcams():
    """Return list of dicts from NPS Solr (all webcam documents)."""
    params = {
        "fl":      SOLR_FIELDS,
        "defType": "edismax",
        "q":       "*:*",
        "fq":      [
            'Category:"Multimedia"',
            'Type:"Webcam"',
        ],
        "rows":    500,
        "wt":      "json",
    }
    r = SESSION.get(SOLR_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    docs = data["response"]["docs"]
    print(f"  Solr returned {len(docs)} webcam documents "
          f"(total found: {data['response']['numFound']})")
    return docs


# ── Per-cam image URL extraction ───────────────────────────────────────────

LIVE_PATTERNS = [
    # NPS webcam directories
    r"https?://www\.nps\.gov/webcams-\w+/[\w.\-]+\.jpe?g",
    # Grand Canyon (pixelcaster)
    r"https?://cdn\.pixelcaster\.com/[^\s\"']+\.jpe?g",
    # USGS volcano cams
    r"https?://volcanoes\.usgs\.gov/[^\s\"']+\.jpe?g",
    # USGS streamgage images
    r"https?://usgs-nims-images\.s3\.amazonaws\.com/[^\s\"']+\.jpe?g",
    # UDOT traffic cams
    r"https?://udottraffic\.utah\.gov/[^\s\"']+\.jpe?g",
    # Bar Harbor cams (Acadia area)
    r"https?://[^\s\"']*barharborcam[^\s\"']+\.jpe?g",
    # Glacier.org
    r"https?://glacier\.org/webcam/[^\s\"']+\.jpe?g",
    # NPS featurecontent (Big Bend etc.)
    r"https?://www\.nps\.gov/featurecontent/ard/webcams/images/[^\s\"']+\.jpe?g",
    # Air-resource.net (Smoky Mountains etc.)
    r"https?://[^\s\"']*air-resource\.net/[^\s\"']*\.jpe?g",
]

LIVE_IMG_HOSTS = (
    "webcams-", "pixelcaster", "volcanoes.usgs.gov",
    "usgs-nims-images", "udottraffic", "barharborcam",
    "glacier.org/webcam", "featurecontent/ard", "air-resource",
)


def fetch_live_url(view_url):
    """
    Return {"type": "img", "url": ...} or {"type": "youtube", "videoId": ...}
    by scraping the NPS webcam view page. Returns None if nothing found.
    """
    full_url = NPS_BASE + view_url if view_url.startswith("/") else view_url
    try:
        r = SESSION.get(full_url, timeout=12)
        html  = r.text
        soup  = BeautifulSoup(html, "html.parser")
    except Exception as e:
        print(f"    fetch error: {e}")
        return None

    # 1. YouTube iframe
    yt = soup.find("iframe", src=re.compile(r"youtube\.com/embed"))
    if yt:
        m = re.search(r"embed/([A-Za-z0-9_-]{11})", yt["src"])
        if m:
            return {"type": "youtube", "videoId": m.group(1)}

    # 2. img tag with a known live-cam host
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if any(h in src for h in LIVE_IMG_HOSTS):
            if not src.startswith("http"):
                src = NPS_BASE + src
            return {"type": "img", "url": src}

    # 3. webCamURL embedded in page JS
    m = re.search(r"webCamURL=([^&\"'\s]+)", html)
    if m:
        url = unquote(m.group(1))
        if url.startswith("http"):
            return {"type": "img", "url": url}

    # 4. Regex scan for known JPEG patterns
    for pat in LIVE_PATTERNS:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return {"type": "img", "url": m.group(0)}

    # 5. Any img on nps.gov ending in .jpg that isn't a logo/icon
    for img in soup.find_all("img", src=re.compile(r"\.jpe?g$", re.I)):
        src = img.get("src", "")
        if not src.startswith("http"):
            src = NPS_BASE + src
        if "nps.gov" in src and not any(x in src for x in (
            "/common/", "/media/", "/theme/", "logo", "icon", "banner"
        )):
            return {"type": "img", "url": src}

    return None


# ── SpotCameras scraping ───────────────────────────────────────────────────

def fetch_spotcameras_listing(state_path):
    """
    Return list of (detail_url, description, thumb_url) for all cams on a
    SpotCameras state listing page. All data comes from the single listing
    request — no per-cam detail fetches needed.
    """
    url = SPOTCAMERAS_BASE + state_path
    r = SESSION.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for item in soup.find_all("div", class_="cbp-item"):
        a = item.find("a", href=True)
        desc_div = item.find("div", class_="cbp-l-grid-agency-desc")
        img = item.find("img", src=True)
        if a and desc_div and img:
            results.append((a["href"], desc_div.get_text(strip=True), img["src"]))
    return results


def run_spotcameras(args, existing, existing_keys):
    """Scrape SpotCameras and return (new_by_state dict, skipped count)."""
    new_by_state = {}
    skipped = 0

    for state, path in SPOTCAMERAS_STATES.items():
        print(f"\nFetching SpotCameras listing for {state}: {SPOTCAMERAS_BASE + path}")
        try:
            cams = fetch_spotcameras_listing(path)
        except Exception as e:
            print(f"  ERROR fetching listing: {e}")
            continue

        if args.limit:
            cams = cams[: args.limit]

        total = len(cams)
        print(f"  Found {total} cams on listing page")

        for i, (detail_path, description, thumb_src) in enumerate(cams, 1):
            prefix = f"  [{i:3}/{total}]"

            # Parse name / location from "City, County, State, Country - View description"
            if " - " in description:
                location, view = description.split(" - ", 1)
                name = f"{location} - {view}"
            else:
                location = description
                name = description

            img_url = (
                thumb_src if thumb_src.startswith("http")
                else SPOTCAMERAS_BASE + thumb_src
            )

            key = img_url
            if key in existing_keys:
                print(f"{prefix} SKIP (duplicate) — {name[:55]}")
                continue

            cam_entry = {
                "name": name,
                "location": location,
                "type": "img",
                "url": img_url,
            }
            new_by_state.setdefault(state, []).append(cam_entry)
            existing_keys.add(key)
            print(f"{prefix} {state}  {name[:55]}")

    return new_by_state, skipped


# ── State resolution ───────────────────────────────────────────────────────

def state_from_park(park_name):
    if not park_name:
        return None
    low = park_name.lower()
    for key, st in NAME_STATE.items():
        if key in low:
            return st
    return None


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["nps", "spotcameras"], default="nps",
                    help="Data source: nps (default) or spotcameras")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print results without writing webcams.json")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N webcams (0 = all)")
    args = ap.parse_args()

    out_path = Path(__file__).parent / "webcams.json"

    # Load existing data
    existing = {}
    if out_path.exists():
        existing = json.loads(out_path.read_text())
    existing_keys = {
        c.get("url") or c.get("videoId")
        for cams in existing.values()
        for c in cams
    }

    if args.source == "spotcameras":
        new_by_state, skipped = run_spotcameras(args, existing, existing_keys)
    else:
        print("Querying NPS Solr API for webcams…")
        docs = fetch_all_webcams()

        if args.limit:
            docs = docs[: args.limit]

        new_by_state = {}
        skipped = 0
        total = len(docs)

        for i, doc in enumerate(docs, 1):
            title    = doc.get("Title", "NPS Webcam")
            park     = doc.get("Parks", "")
            page_url = doc.get("PageURL", "")

            state = state_from_park(park)

            prefix = f"[{i:3}/{total}]"

            if not state:
                print(f"{prefix} SKIP (no state) — {park or title}")
                skipped += 1
                continue

            if not page_url:
                print(f"{prefix} SKIP (no URL) — {title}")
                skipped += 1
                continue

            print(f"{prefix} {state}  {title[:55]}")

            cam_data = fetch_live_url(page_url)
            if not cam_data:
                print(f"         └─ no live URL found")
                skipped += 1
                continue

            key = cam_data.get("url") or cam_data.get("videoId")
            if key in existing_keys:
                print(f"         └─ already in webcams.json")
                continue

            cam_entry = {"name": title, "location": park, **cam_data}
            new_by_state.setdefault(state, []).append(cam_entry)
            existing_keys.add(key)
            print(f"         └─ {cam_data['type']}: {key[:80]}")

            time.sleep(0.15)  # be polite to nps.gov

    # ── Merge ──────────────────────────────────────────────────────────────
    merged = dict(existing)
    added = 0
    for state, cams in new_by_state.items():
        merged.setdefault(state, []).extend(cams)
        added += len(cams)

    merged = dict(sorted(merged.items()))

    print(f"\n─────────────────────────────────────────────")
    print(f"Added:   {added} new webcams")
    print(f"Skipped: {skipped}")
    print(f"Total:   {sum(len(v) for v in merged.values())} cams across {len(merged)} states")

    if args.dry_run:
        print("(dry run — nothing written)")
        for st, cams in sorted(new_by_state.items()):
            for c in cams:
                key = c.get("url") or c.get("videoId", "")
                print(f"  {st}  {c['name'][:50]}  {key[:70]}")
    else:
        out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
        print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
