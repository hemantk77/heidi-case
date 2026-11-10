"""
Heidi case - Task 1, step 2: clean, resolve entities and pre-qualify.

1. Berlin filter     - keep only addresses with a Berlin postcode (10115-14199) and "Berlin".
2. District fill-in  - Google leaves the Bezirk blank for ~1/3 of places. We LEARN a
                       postcode -> Bezirk map from the 2/3 that have one (majority vote) and
                       fill the gaps from it. No external data needed.
3. Entity resolution - one company can have several Google listings (branches, duplicates).
                       Key = website domain (unless it is a shared host like a franchise or
                       website builder), else phone number, else normalised name. Branches are
                       grouped under one company with a location count, not thrown away.
4. Relevance tier    - rule-based pre-filter (verified later per website by the LLM step):
                       core      = name says it manages property (Verwaltung, WEG, Property Mgmt)
                       probable  = real-estate firm found by 2+ different management keywords
                       check     = real-estate firm, management not yet confirmed
                       cooperative = Wohnungsgenossenschaft (big portfolios, often in-house billing)
                       large_landlord = municipal/listed housing companies (separate enterprise motion)
                       excluded  = only with EVIDENCE: trades, lawyers, buildings, franchise agents, competitors
"""
import csv, re, collections, sys
from pathlib import Path
from urllib.parse import urlparse
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

D = Path(__file__).resolve().parent / "data"
rows = list(csv.DictReader(open(D / "places_raw.csv", encoding="utf-8-sig")))

PLZ_RE = re.compile(r"\b(1[0-4]\d{3})\b")
def plz_of(r):
    if r["plz"]: return r["plz"]
    m = PLZ_RE.search(r["address"]); return m.group(1) if m else ""

# ---- 1. Berlin filter (Berlin postcodes run 10115-14199)
berlin = []
for r in rows:
    p = plz_of(r)
    if "Berlin" in r["address"] and p and 10115 <= int(p) <= 14199:
        r["plz"] = p; berlin.append(r)

# ---- 2. learn postcode -> Bezirk
BEZIRKE = {"Mitte","Friedrichshain-Kreuzberg","Pankow","Charlottenburg-Wilmersdorf","Spandau",
           "Steglitz-Zehlendorf","Tempelhof-Schöneberg","Neukölln","Treptow-Köpenick",
           "Marzahn-Hellersdorf","Lichtenberg","Reinickendorf"}
votes = collections.defaultdict(collections.Counter)
for r in berlin:
    b = r["bezirk"]
    if not b:  # Google sometimes writes "Berlin-Bezirk X" into the address instead
        m = re.search(r"Berlin-Bezirk ([\wäöüÄÖÜß-]+)", r["address"]); b = m.group(1) if m else ""
        r["bezirk"] = b
    if b in BEZIRKE: votes[r["plz"]][b] += 1
plz_map = {p: c.most_common(1)[0][0] for p, c in votes.items()}
filled = 0
for r in berlin:
    if r["bezirk"] not in BEZIRKE:
        r["bezirk"] = plz_map.get(r["plz"], ""); filled += 1 if r["bezirk"] else 0
        r["bezirk_method"] = "learned from postcode" if r["bezirk"] else "unknown"
    else:
        r["bezirk_method"] = "Google"

# ---- 3. entity resolution
SHARED_HOSTS = ("von-poll.com","engelvoelkers.com","facebook.com","instagram.com","google.com","business.site",
    "jimdo","wixsite","immobilienscout24","immowelt","linkedin.com","dahler.com","remax","sparkasse",
    "volksbank","dkb.de","postbank","t-online.de","gmx","web.de","wordpress.com","homepage.","ebay")
def domain(url):
    if not url: return ""
    h = urlparse(url if "//" in url else "http://" + url).netloc.lower().split(":")[0]
    h = re.sub(r"^www\d?\.", "", h)
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 and parts[-2] not in ("co", "com") else h
def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    if d.startswith("0"): d = "49" + d[1:]
    return d
LEGAL = r"\b(gmbh|mbh|ug|haftungsbeschränkt|ag|kg|co|ohg|gbr|e\.?\s?k|e\.?\s?v|e\.?\s?g|inh|inhaber)\b"
def norm_name(n):
    n = n.lower().replace("&", " ")
    n = re.sub(LEGAL, " ", n); n = re.sub(r"[^a-zäöüß0-9 ]", " ", n)
    n = re.sub(r"\b(hausverwaltung|immobilienverwaltung|verwaltung|immobilien|berlin)\b", " ", n)
    return " ".join(n.split())

for r in berlin:
    r["domain"] = domain(r["website"])
    shared = any(s in r["domain"] for s in SHARED_HOSTS) if r["domain"] else True
    if r["domain"] and not shared: r["entity_key"], r["key_type"] = "d:" + r["domain"], "domain"
    elif norm_phone(r["phone"]): r["entity_key"], r["key_type"] = "p:" + norm_phone(r["phone"]), "phone"
    else: r["entity_key"], r["key_type"] = "n:" + norm_name(r["name"]) + "|" + r["plz"], "name+plz"

# union-find: also merge listings that share a phone OR a domain (catches "same firm, two websites")
parent = {}
def find(x):
    parent.setdefault(x, x)
    while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
    return x
def union(a, b): parent[find(a)] = find(b)
by_phone = collections.defaultdict(list)
for r in berlin:
    find(r["entity_key"])
    ph = norm_phone(r["phone"])
    if ph and len(ph) > 8: by_phone[ph].append(r["entity_key"])
for keys in by_phone.values():
    if len(set(keys)) <= 3:     # a phone shared by >3 'companies' is a call centre / franchise hotline - don't merge
        for k in keys[1:]: union(k, keys[0])
groups = collections.defaultdict(list)
for r in berlin: groups[find(r["entity_key"])].append(r)

# ---- 4. relevance tiers
CORE = re.compile(r"(haus|immobilien|grundst\w*|grundbesitz|wohnungs|wohn|miet|weg|liegenschafts|objekt|geb[aä]ude|property|real estate|immobilien[\s-]?)[\s-]?(und\s\w+\s?)?verwalt"
    r"|hausverw|weg[\s-]verw|\bweg\b|property\s?manag|immobilien[\s-]?management|immobilienmanagement|immobilien[\s-]?service"
    r"|\bverwaltungs?(gesellschaft|-gmbh| gmbh)|asset\s?manag", re.I)
NOT_CORE = re.compile(r"kirchhofsverw|kirchhofverw|friedhof|unternehmensverw|verm[oö]gensverw|nachlass|insolvenz|steuer|zwangsverw", re.I)
HOUSE = re.compile(r"haus[\s&-]|haus\b|hausverw|imm\w{0,2}bilien|grundbesitz|grundst|wohnung|liegenschaft|objekt", re.I)
TYPO = re.compile(r"imm\w{0,2}bilienverw|immomanagement|verwalten\b", re.I)
COOP = re.compile(r"genossenschaft|\beg\b|e\.\s?g\.|bauverein|wohnungs-?verein", re.I)
LARGE = re.compile(r"gewobag|degewo|howoge|gesobau|stadt und land|\bwbm\b|berlinovo|vonovia|deutsche wohnen|adler group|"
    r"heimstaden|akelius|covivio|grand city|gewosüd|gesewo|buwog", re.I)
EXCL_TYPES = {"general_contractor","plumber","lawyer","painter","moving_company","health","insurance_agency",
    "travel_agency","lodging","educational_institution","manufacturer","apartment_building","apartment_complex",
    "condominium_complex","housing_complex","local_government_office","government_office","electrician",
    "roofing_contractor","locksmith","storage","bank","restaurant","store","car_repair","hotel","cemetery","church"}
EXCL_NAME = re.compile(r"rechtsanw|anwalt|kanzlei|notar|steuerber|bauunternehm|baufirma|hochbau|tiefbau|bautr[aä]g|baubetreu|"
    r"sanit[aä]r|heizungsbau|klempner|haustechnik|gartenbau|garten|innenausbau|wohnungsaufl|elektro|malerei|maler\b|reinigung|geb[aä]udereinig|hausmeister|hauswart|umz[uü]g|entr[uü]mpel|"
    r"schl[uü]ssel|dachdeck|architekt|gutacht|sachverst|versicher|\bbank\b|finanzberat|mieterverein|mieterberatung|"
    r"mieterschutz|haus\s?(und|&)\s?grund|bezirksamt|jobcenter|wohnungsamt|von poll|engel\s?(&|und)\s?v|remax|re/max|dahler|"
    r"immonet|immoscout|coworking|regus|\biwg\b|spaces|hostel|apartments?\b|serviced", re.I)
MAKLER = re.compile(r"makler|maegler|agentur|invest|development|projekt|kapitalanlage", re.I)
COMPETITOR = re.compile(r"\bista\b|techem|brunata|minol|\bkalo\b|kugu|qivalo|metrona|messdienst|heizkosten|waitly|casavi|facilioo", re.I)

companies = []
for key, rs in groups.items():
    rs.sort(key=lambda r: -int(r["reviews"] or 0))       # main listing = most reviewed
    m = rs[0]
    names = " ".join(r["name"] for r in rs); types = {r["primary_type"] for r in rs}
    kws = set("|".join(r["found_by_keywords"] for r in rs).split("|"))
    house = HOUSE.search(names)
    core = (CORE.search(names) or TYPO.search(names)) and not (NOT_CORE.search(names) and not house) and not (types & {"cemetery","church"})
    if COMPETITOR.search(names) and not core: tier, why = "excluded", "metering competitor or proptech vendor (not a customer)"
    elif LARGE.search(names): tier, why = "large_landlord", "municipal / listed housing company (enterprise tender, in-house mgmt)"
    elif COOP.search(names): tier, why = "cooperative", "housing cooperative (large own portfolio)"
    elif core: tier, why = "core", "name indicates property management"
    elif EXCL_NAME.search(names) or (types & EXCL_TYPES) or (NOT_CORE.search(names) and not house):
        tier, why = "excluded", "evidence it is not a property manager (trade/legal/building/franchise agent/other)"
    elif MAKLER.search(names): tier, why = "check", "estate agent/investor - management offer to be verified on website"
    elif len(kws) >= 2: tier, why = "probable", "found by 2+ different management keywords"
    else: tier, why = "check", "listed under 'Hausverwaltung' on Google - verify on website"
    total_reviews = sum(int(r["reviews"] or 0) for r in rs)
    rated = [(float(r["rating"]), int(r["reviews"] or 0)) for r in rs if r["rating"]]
    avg = round(sum(a*b for a,b in rated)/max(1,sum(b for _,b in rated)), 2) if rated and sum(b for _,b in rated) else (rated[0][0] if rated else "")
    companies.append({
        "company_id": "", "company_name": m["name"], "tier": tier, "tier_reason": why,
        "bezirk": m["bezirk"], "ortsteil": m["ortsteil"], "plz": m["plz"], "address": m["address"],
        "website": m["website"] or next((r["website"] for r in rs if r["website"]), ""),
        "domain": m["domain"], "phone": m["phone"] or next((r["phone"] for r in rs if r["phone"]), ""),
        "google_rating": avg, "google_reviews": total_reviews,
        "berlin_locations": len(rs), "other_listing_names": " | ".join(sorted({r["name"] for r in rs[1:]}))[:300],
        "found_by_keywords": "|".join(sorted(k for k in kws if k)), "keyword_hits": len([k for k in kws if k]),
        "primary_type": m["primary_type"], "maps_url": m["maps_url"], "lat": m["lat"], "lng": m["lng"],
        "dedup_key_type": m["key_type"], "bezirk_method": m["bezirk_method"],
        "source": "Google Places API",
    })

order = {"core":0,"probable":1,"cooperative":2,"large_landlord":3,"check":4,"excluded":5}
companies.sort(key=lambda c: (order[c["tier"]], -int(c["google_reviews"])))
for i, c in enumerate(companies, 1): c["company_id"] = f"HV{i:04d}"
with open(D / "companies.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(companies[0].keys())); w.writeheader(); w.writerows(companies)

print(f"raw places:              {len(rows)}")
print(f"with Berlin postcode:    {len(berlin)}  (dropped {len(rows)-len(berlin)} outside Berlin)")
print(f"Bezirk filled from PLZ:  {filled}  (postcode map learned from {len(plz_map)} postcodes)")
print(f"unique companies:        {len(companies)}  (merged {len(berlin)-len(companies)} duplicate/branch listings)")
print("tiers:", collections.Counter(c["tier"] for c in companies))
print("multi-location firms:", sum(c["berlin_locations"]>1 for c in companies))
print("bezirk unknown:", sum(not c["bezirk"] for c in companies))
