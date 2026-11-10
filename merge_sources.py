"""
Heidi case - Task 1, step 3: merge the second source (Gelbe Seiten sample) and estimate coverage.

Gelbe Seiten lists 609 'Hausverwaltung' entries for Berlin. We read the first results page
(50 entries) as a SAMPLE - no bulk scraping - and match each one against the Google data
by website domain, phone number or street address. The match rate is an estimate of how
much of the market Google covers (a capture-recapture idea), and the misses are added as
extra rows with source = 'Gelbe Seiten'.
"""
import csv, json, re, sys
from pathlib import Path
from urllib.parse import urlparse
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
D = Path(__file__).resolve().parent / "data"

def dom(u):
    if not u: return ""
    h = urlparse(u if "//" in u else "http://" + u).netloc.lower()
    return re.sub(r"^www\d?\.", "", h)
def ph(p):
    d = re.sub(r"\D", "", p or ""); return "49" + d[1:] if d.startswith("0") else d
def street(a):
    m = re.match(r"\s*([^,]+?)\s*(\d+\s*\w?)\s*,", a or "")
    return (re.sub(r"(str\.|straße|strasse)", "str", m.group(1).lower()).replace(" ", ""), m.group(2).replace(" ", "").lower()) if m else None

companies = list(csv.DictReader(open(D / "companies.csv", encoding="utf-8-sig")))
raw = list(csv.DictReader(open(D / "places_raw.csv", encoding="utf-8-sig")))
NOT_HV = re.compile(r"partei|willy-brandt|energiegesell|i\.\s?g\.|\bc/o\b|construction", re.I)

# index every Google listing (incl. branches) -> company
by_d, by_p, by_s = {}, {}, {}
for c in companies:
    if c["domain"]: by_d.setdefault(c["domain"], c)
    if c["phone"]: by_p.setdefault(ph(c["phone"]), c)
    by_s.setdefault(street(c["address"]), c)
for r in raw:   # branch listings map to their company through domain/phone
    s = street(r["address"])
    tgt = by_d.get(dom(r["website"])) or by_p.get(ph(r["phone"]))
    if tgt and s: by_s.setdefault(s, tgt)

gs = json.load(open(D / "gelbeseiten_sample.json", encoding="utf-8"))
seen, sample, matched, relevant, rel_matched, added = set(), 0, 0, 0, 0, []
for name, addr, phone, web in gs:
    k = ph(phone) or name
    if k in seen: continue
    seen.add(k); sample += 1
    hv = not NOT_HV.search(name)
    relevant += hv
    c = by_d.get(dom(web)) if web else None
    c = c or (by_p.get(ph(phone)) if phone else None) or by_s.get(street(addr))
    if c:
        matched += 1; rel_matched += hv
        c["source"] = "Google Places + Gelbe Seiten"
    elif hv:
        plz = (re.search(r"\b(1[0-4]\d{3})\b", addr) or [""])[0] if re.search(r"\b(1[0-4]\d{3})\b", addr) else ""
        ort = (re.search(r"\(([^)]+)\)", addr) or [None, ""])[1] if "(" in addr else ""
        added.append({**{k: "" for k in companies[0]}, "company_name": name, "tier": "core" if re.search(r"verwalt|property|real estate manag", name, re.I) else "check",
            "tier_reason": "found only in Gelbe Seiten (Hausverwaltung category)", "ortsteil": ort, "plz": plz,
            "address": re.sub(r"\s*\([^)]*\)", "", addr), "website": web, "domain": dom(web), "phone": phone,
            "berlin_locations": 1, "source": "Gelbe Seiten", "dedup_key_type": "n/a", "bezirk_method": "learned from postcode"})

# Bezirk for the added rows from the learned postcode map
plz_map = {}
for c in companies:
    if c["plz"] and c["bezirk"]: plz_map.setdefault(c["plz"], c["bezirk"])
for a in added: a["bezirk"] = plz_map.get(a["plz"], "")

allc = companies + added
order = {"core":0,"probable":1,"cooperative":2,"large_landlord":3,"check":4,"excluded":5}
allc.sort(key=lambda c: (order[c["tier"]], -int(c["google_reviews"] or 0)))
for i, c in enumerate(allc, 1): c["company_id"] = f"HV{i:04d}"
with open(D / "companies_merged.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(allc[0].keys())); w.writeheader(); w.writerows(allc)

cov = rel_matched / relevant
json.dump({"gs_total_listed": 609, "gs_sample_unique": sample, "gs_sample_matched": matched,
           "gs_sample_relevant": relevant, "gs_relevant_matched": rel_matched, "google_coverage_est": round(cov, 3),
           "added_from_gs": len(added)}, open(D / "coverage.json", "w"), indent=2)
print(f"Gelbe Seiten sample: {sample} unique, {matched} also in Google ({matched/sample:.0%})")
print(f"Property managers only: {rel_matched}/{relevant} matched -> Google coverage ~{cov:.0%}")
print(f"Added from Gelbe Seiten: {len(added)} -> total companies {len(allc)}")
