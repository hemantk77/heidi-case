"""
Heidi case - Task 1, step 1: collect Berlin property managers from Google Places API (New).

Technique: adaptive QUADTREE search.
  Google Text Search returns at most 60 results (3 pages x 20) per query. A single query
  for "Hausverwaltung Berlin" therefore silently misses most of the market. We split Berlin
  into a 4x4 grid of rectangles and, whenever a cell returns the full 60, split that cell
  into 4 smaller cells and search again - so dense areas (Mitte, Charlottenburg) get fine
  cells and sparse areas (Spandau edges) stay coarse. Every request is cached on disk, so
  re-runs cost nothing, and a saturation log records how many NEW companies each request found.

Usage (Windows, in this folder):
  python collect_places.py --test        # 1 cheap request, prints results
  python collect_places.py               # full run (budget-capped)
  python collect_places.py --max-requests 400
Only the Python standard library is used - nothing to pip install.
"""
import argparse, csv, hashlib, json, sys, time, urllib.request, urllib.error
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"
CACHE = OUT / "cache_places"
URL = "https://places.googleapis.com/v1/places:searchText"

# Berlin bounding box (slightly generous; results outside Berlin are filtered by address)
BERLIN = (52.3383, 13.0884, 52.6755, 13.7611)  # low_lat, low_lng, high_lat, high_lng
KEYWORDS = ["Hausverwaltung", "Immobilienverwaltung", "WEG-Verwaltung", "Mietverwaltung"]
MAX_DEPTH = 4          # 4x4 start grid -> up to 4 further splits (~600 m cells at the finest)
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
    "places.location", "places.websiteUri", "places.nationalPhoneNumber",
    "places.internationalPhoneNumber", "places.rating", "places.userRatingCount",
    "places.types", "places.primaryType", "places.businessStatus", "places.googleMapsUri",
    "nextPageToken",
])


def load_key():
    env = HERE / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("GOOGLE_PLACES_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("GOOGLE_PLACES_API_KEY not found in .env")


class Client:
    def __init__(self, key, max_requests):
        self.key, self.max_requests = key, max_requests
        self.billed = 0      # real API calls this run
        self.cached = 0      # served from disk

    def search(self, keyword, rect, page_token=None):
        body = {
            "textQuery": f"{keyword} Berlin",
            "languageCode": "de", "regionCode": "DE", "pageSize": 20,
            "locationRestriction": {"rectangle": {
                "low": {"latitude": rect[0], "longitude": rect[1]},
                "high": {"latitude": rect[2], "longitude": rect[3]}}},
        }
        if page_token:
            body["pageToken"] = page_token
        h = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        f = CACHE / f"{h}.json"
        if f.exists():
            self.cached += 1
            return json.loads(f.read_text(encoding="utf-8"))
        if self.billed >= self.max_requests:
            raise BudgetExceeded()
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")
                if e.code in (429, 500, 503) and attempt < 3:
                    time.sleep(2 ** attempt); continue
                sys.exit(f"\nAPI error {e.code}:\n{msg}\n")
            except urllib.error.URLError as e:
                if attempt < 3:
                    time.sleep(2 ** attempt); continue
                sys.exit(f"\nNetwork error: {e}\n")
        self.billed += 1
        f.write_text(json.dumps({"request": body, "response": data}, ensure_ascii=False), encoding="utf-8")
        return {"request": body, "response": data}


class BudgetExceeded(Exception):
    pass


def split(rect):
    a, b, c, d = rect
    mlat, mlng = (a + c) / 2, (b + d) / 2
    return [(a, b, mlat, mlng), (a, mlng, mlat, d), (mlat, b, c, mlng), (mlat, mlng, c, d)]


def grid(rect, n):
    a, b, c, d = rect
    dl, dg = (c - a) / n, (d - b) / n
    return [(a + i * dl, b + j * dg, a + (i + 1) * dl, b + (j + 1) * dg) for i in range(n) for j in range(n)]


def district_of(components):
    """Google gives Berlin's Bezirk as sublocality_level_1 and the Ortsteil as sublocality_level_2."""
    bezirk = ortsteil = plz = ""
    for c in components or []:
        t = c.get("types", [])
        if "sublocality_level_1" in t:
            bezirk = c.get("longText", "").replace("Bezirk ", "")
        elif "sublocality_level_2" in t:
            ortsteil = c.get("longText", "")
        elif "postal_code" in t:
            plz = c.get("longText", "")
    return bezirk, ortsteil, plz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="one request around Steglitz, print results")
    ap.add_argument("--max-requests", type=int, default=900, help="hard cap on billed API calls")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True); CACHE.mkdir(exist_ok=True)
    client = Client(load_key(), 1 if args.test else args.max_requests)

    if args.test:
        res = client.search("Hausverwaltung", (52.445, 13.30, 52.475, 13.35))["response"]
        places = res.get("places", [])
        print(f"OK - {len(places)} results (more pages: {'yes' if res.get('nextPageToken') else 'no'})\n")
        for p in places[:10]:
            print(f"- {p.get('displayName', {}).get('text')} | {p.get('formattedAddress')} | "
                  f"{p.get('websiteUri', '-')} | rating {p.get('rating', '-')} ({p.get('userRatingCount', 0)})")
        print("\nTest worked. Now run:  python collect_places.py")
        return

    places, hits = {}, {}      # place_id -> place ; place_id -> set of keywords that found it
    sat_rows, log_rows = [], []
    try:
        for kw in KEYWORDS:
            stack = [(r, 0) for r in grid(BERLIN, 4)]
            while stack:
                rect, depth = stack.pop(0)
                token, got = None, 0
                for page in range(3):
                    res = client.search(kw, rect, token)["response"]
                    batch = res.get("places", [])
                    got += len(batch)
                    new = 0
                    for p in batch:
                        pid = p["id"]
                        if pid not in places:
                            places[pid] = p; new += 1
                        hits.setdefault(pid, set()).add(kw)
                    sat_rows.append([client.billed + client.cached, kw, depth, len(batch), new, len(places)])
                    token = res.get("nextPageToken")
                    if not token:
                        break
                saturated = got >= 60
                split_it = saturated and depth < MAX_DEPTH
                log_rows.append([kw, depth, *[round(x, 5) for x in rect], got, "split" if split_it else "done"])
                print(f"[{kw:<20}] depth {depth} cell -> {got:>2} results | unique total {len(places):>4} "
                      f"| billed {client.billed}{'  -> SPLIT' if split_it else ''}")
                if split_it:
                    stack.extend((r, depth + 1) for r in split(rect))
    except BudgetExceeded:
        print(f"\nStopped at the budget cap of {args.max_requests} billed requests. "
              f"Re-run with a higher --max-requests to continue (cached requests are free).")

    # write outputs
    rows = []
    for pid, p in places.items():
        addr = p.get("formattedAddress", "")
        bezirk, ortsteil, plz = district_of(p.get("addressComponents"))
        rows.append({
            "place_id": pid,
            "name": p.get("displayName", {}).get("text", ""),
            "bezirk": bezirk, "ortsteil": ortsteil, "plz": plz,
            "address": addr,
            "in_berlin": "yes" if "Berlin" in addr else "no",
            "website": p.get("websiteUri", ""),
            "phone": p.get("internationalPhoneNumber", "") or p.get("nationalPhoneNumber", ""),
            "rating": p.get("rating", ""),
            "reviews": p.get("userRatingCount", 0),
            "primary_type": p.get("primaryType", ""),
            "types": "|".join(p.get("types", [])),
            "business_status": p.get("businessStatus", ""),
            "lat": p.get("location", {}).get("latitude", ""),
            "lng": p.get("location", {}).get("longitude", ""),
            "maps_url": p.get("googleMapsUri", ""),
            "found_by_keywords": "|".join(sorted(hits.get(pid, []))),
            "source": "Google Places API",
        })
    with open(OUT / "places_raw.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["place_id"])
        w.writeheader(); w.writerows(rows)
    with open(OUT / "saturation.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["request_no", "keyword", "depth", "results", "new_unique", "cumulative_unique"]); w.writerows(sat_rows)
    with open(OUT / "cells_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["keyword", "depth", "low_lat", "low_lng", "high_lat", "high_lng", "results", "action"]); w.writerows(log_rows)

    in_berlin = sum(r["in_berlin"] == "yes" for r in rows)
    print(f"\nDone. {len(rows)} unique places ({in_berlin} with a Berlin address).")
    print(f"Billed requests this run: {client.billed} | served from cache: {client.cached}")
    print(f"Files: data/places_raw.csv, data/saturation.csv, data/cells_log.csv")


if __name__ == "__main__":
    main()
