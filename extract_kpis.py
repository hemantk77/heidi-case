"""
Heidi case - Task 2, step 2: extract lead KPIs from the crawled websites.

Two layers, on purpose:
 A) DETERMINISTIC signals (regex on raw HTML/text - free, reproducible, no hallucination):
    association badges (VDIV/DDIV, IVD), 'zertifizierter Verwalter', §34c GewO licence,
    Handelsregister number, software fingerprints (casavi, facilioo, immoware24, DOMUS, ...),
    metering-competitor mentions (ista, Techem, Brunata-Metrona, Minol, KALO, ...), hiring signals.
 B) LLM extraction (Claude) for facts that need reading comprehension: is it really a property
    manager, which services, units managed, employees, founding year, managing directors,
    how heating-cost billing is handled, tenant/owner portal.
    EVIDENCE-GROUNDED: every value must come with a verbatim quote. Code then checks that the
    quote really occurs in the page text; if it does not, the value is discarded and flagged.

Usage:  python extract_kpis.py --limit 20     (test)
        python extract_kpis.py                (all; resumable, results cached in data/llm/)
"""
import argparse, csv, gzip, hashlib, json, re, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_websites import to_text, WEB
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

HERE = Path(__file__).resolve().parent; D = HERE / "data"; LLM = D / "llm"; LLM.mkdir(exist_ok=True)
MODELS = ["claude-haiku-5-5", "claude-haiku-4-5"]          # small + cheap; falls back if first is unavailable
PRICE_IN, PRICE_OUT = 1.0, 5.0                             # USD per million tokens (estimate - check console)

def key():
    for l in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if l.strip().startswith("ANTHROPIC_API_KEY="): return l.split("=", 1)[1].strip().strip('"\'')
    sys.exit("ANTHROPIC_API_KEY missing in .env")

def cached_page(url):
    f = WEB / f"{hashlib.sha1(url.encode()).hexdigest()[:20]}.json.gz"
    return json.loads(gzip.decompress(f.read_bytes()).decode("utf-8")) if f.exists() else None

# ---------------- A) deterministic signals
SOFTWARE = {"casavi": r"casavi", "facilioo": r"facilioo", "immoware24": r"immoware", "DOMUS": r"domus[- ]?software|domus ?4000|domus ?navi",
    "PowerHaus": r"powerhaus", "Haufe axera": r"axera", "Karthago": r"karthago", "Aareon/Wodis": r"aareon|wodis",
    "etg24": r"etg24", "objego": r"objego", "immocloud": r"immocloud", "Allthings": r"allthings", "Immomio": r"immomio",
    "Hausify": r"hausify", "Hausgold": r"hausgold"}
COMPETITORS = {"ista": r"\bista\b", "Techem": r"techem", "Brunata-Metrona": r"brunata|metrona", "Minol": r"\bminol\b",
    "KALO": r"\bkalo\b|kalorimeta", "Qivalo": r"qivalo", "KUGU": r"\bkugu\b", "Heidi": r"heidi[- ]?systems",
    "MeAB": r"\bmeab\b", "WDV-Molliné": r"moll?in[eé]", "Messdienst (unnamed)": r"messdienst"}
HIRING_BILLING = r"nebenkostenabrechn|betriebskostenabrechn|heizkostenabrechn|objektbuchhalt|buchhalter|immobilienbuchhalt"
HIRING_ANY = r"(wir suchen|stellenangebot|stellenanzeige|offene stellen|verstärkung|\(m/w/d\)|\(w/m/d\)|\(m/w/x\)|\(d/m/w\))"

def signals(pages):
    html_all = " ".join(p["html"] for p in pages.values()).lower()
    text = {k: p["text"] for k, p in pages.items()}; tl = " ".join(text.values()).lower()
    imp = text.get("impressum", "") or tl
    jobs = (text.get("jobs", "") + " " + text.get("home", "")).lower()
    s = {
        "vdiv_member": "yes" if re.search(r"vdiv|ddiv|verband der immobilienverwalter", html_all) else "no",
        "ivd_member": "yes" if re.search(r"\bivd\b|immobilienverband deutschland", html_all) else "no",
        "certified_manager": "yes" if re.search(r"zertifizierte?r?\s+(wohnimmobilien)?verwalter|zertifizierter verwalter|§\s?26a\s?weg", tl) else "no",
        "licence_34c": "yes" if re.search(r"34\s?c", imp, re.I) else "no",
        "hrb": (re.search(r"HR\s?B\s?[\d\s.]{3,9}\s?B?\b", imp) or [""])[0].strip(),
        "software_detected": "|".join(k for k, rx in SOFTWARE.items() if re.search(rx, html_all)),
        "competitor_mentioned": "|".join(k for k, rx in COMPETITORS.items() if re.search(rx, tl)),
        "has_jobs_page": "yes" if "jobs" in pages else "no",
        "hiring_now": "yes" if re.search(HIRING_ANY, jobs) else "no",
        "hiring_billing_roles": "yes" if re.search(HIRING_BILLING, jobs) and re.search(HIRING_ANY, jobs) else "no",
        "has_portal_link": "yes" if "portal" in pages else "no",
        "emails_on_site": "|".join(sorted(set(e.lower() for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+\w", " ".join(text.values()))
                                            if not re.search(r"\.(png|jpe?g|gif|webp)$|example|sentry|wixpress", e, re.I)))[:6]),
    }
    return s

# ---------------- B) LLM extraction
def ev(desc): return {"type": ["string", "null"], "description": desc}
TOOL = {"name": "record_company_facts", "description": "Record facts found on the company website.",
 "input_schema": {"type": "object", "properties": {
   "is_property_manager": {"type": "string", "enum": ["yes", "no", "unclear"], "description": "Does the company offer property management (Hausverwaltung, WEG-, Miet- or Sondereigentumsverwaltung) for third parties or its own portfolio?"},
   "is_property_manager_evidence": ev("Verbatim quote proving it"),
   "main_business": {"type": "string", "enum": ["property_management", "brokerage", "investment_or_development", "housing_owner_landlord", "facility_or_caretaker", "other"]},
   "services": {"type": "array", "items": {"type": "string", "enum": ["WEG-Verwaltung", "Mietverwaltung", "Sondereigentumsverwaltung", "Gewerbeverwaltung", "Makler", "Facility/Hausmeister", "Other"]}},
   "units_managed": {"type": ["integer", "null"], "description": "Number of units managed (Einheiten, Wohneinheiten, Wohnungen, Miet-/Verwaltungseinheiten). If 'über 2.000', use 2000. Null if not stated."},
   "units_evidence": ev("Verbatim quote with the number"),
   "buildings_managed": {"type": ["integer", "null"], "description": "Number of buildings/properties (Objekte, Liegenschaften, Häuser, WEGs). Null if not stated."},
   "buildings_evidence": ev("Verbatim quote with the number"),
   "employees": {"type": ["integer", "null"], "description": "Number of employees/Mitarbeiter if stated."},
   "employees_evidence": ev("Verbatim quote"),
   "founded_year": {"type": ["integer", "null"]}, "founded_evidence": ev("Verbatim quote (e.g. 'seit 1992')"),
   "managing_directors": {"type": "array", "items": {"type": "string"}, "description": "Names of Geschäftsführer/Inhaber/Vorstand from the Impressum."},
   "managing_directors_evidence": ev("Verbatim quote from the Impressum"),
   "heating_billing": {"type": "string", "enum": ["offers_as_service", "mentions_external_provider", "not_mentioned"], "description": "Does the site mention Heiz-/Betriebskostenabrechnung, and is a metering provider (Messdienst) named?"},
   "heating_billing_evidence": ev("Verbatim quote"),
   "portal_or_app": {"type": ["string", "null"], "description": "Name of a tenant/owner portal or app offered (e.g. 'casavi', 'Kundenportal', own app). Null if none."},
   "portal_evidence": ev("Verbatim quote"),
   "segment_note": {"type": "string", "description": "One short English sentence describing the company for a sales rep."}},
  "required": ["is_property_manager", "main_business", "services", "units_managed", "buildings_managed", "employees",
               "founded_year", "managing_directors", "heating_billing", "portal_or_app", "segment_note"]}}

SYSTEM = """You extract facts about a Berlin real-estate company from its own website, for B2B lead qualification.
Rules:
- Use ONLY the website text provided. Never guess, never use outside knowledge, never estimate.
- Every non-null value needs an evidence quote copied EXACTLY (character for character) from the text, max 200 characters. If you cannot quote it, the value must be null / empty / 'not_mentioned'.
- Numbers: copy what is stated. 'über 3.500 Einheiten' -> 3500. Ignore numbers about sales/brokered properties (vermittelte Objekte) - only managed ones.
- 'is_property_manager': 'yes' if the company offers property management (Hausverwaltung, WEG-, Miet-, Sondereigentums- or Immobilienverwaltung) - also when it does brokerage as well. 'no' only if the text shows it does something else (only brokerage, only investment, only caretaking). 'unclear' only if the text says nothing about its business. Always give a quote for 'yes' and 'no'.
- Prefer one contiguous passage as a quote. If you must combine passages, separate them with ' ... '.
- Name metering/billing service providers (Messdienstleister) in heating_billing_evidence when the site names one."""

class RateLimiter:
    def __init__(self, rpm): self.gap, self.lock, self.next = 60.0 / rpm, threading.Lock(), 0.0
    def wait(self):
        with self.lock:
            now = time.time(); t = max(now, self.next); self.next = t + self.gap
        time.sleep(max(0, t - now))

STATE = {"model": None, "in": 0, "out": 0}
def call_claude(k, content, rl):
    models = [STATE["model"]] if STATE["model"] else MODELS
    for model in models:
        body = {"model": model, "max_tokens": 1200, "system": SYSTEM, "tools": [TOOL],
                "tool_choice": {"type": "tool", "name": "record_company_facts"},
                "messages": [{"role": "user", "content": content}]}
        for attempt in range(6):
            rl.wait()
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                headers={"x-api-key": k, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as r: res = json.loads(r.read())
                STATE["model"] = model
                STATE["in"] += res["usage"]["input_tokens"]; STATE["out"] += res["usage"]["output_tokens"]
                return next(b["input"] for b in res["content"] if b["type"] == "tool_use"), model
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")
                if e.code == 404 or ("model" in msg and e.code == 400 and "not_found" in msg): break   # try next model
                if e.code in (429, 500, 502, 503, 529):
                    time.sleep(float(e.headers.get("retry-after") or 2 ** attempt)); continue
                raise RuntimeError(f"API {e.code}: {msg[:300]}")
            except Exception:
                time.sleep(2 ** attempt)
    raise RuntimeError("no model available / repeated failures")

def norm(s): return re.sub(r"\s+", " ", re.sub(r"[­​]", "", s or "")).strip().lower()
def norm_loose(s): return re.sub(r"[^\wäöüß]+", "", norm(s))

def verify(facts, fulltext):
    """Drop any value whose evidence quote is not in the page text. Returns counts for validation."""
    nt, nl = norm(fulltext), norm_loose(fulltext)
    checked = failed = 0
    pairs = [("is_property_manager", "is_property_manager_evidence"), ("units_managed", "units_evidence"),
             ("buildings_managed", "buildings_evidence"), ("employees", "employees_evidence"),
             ("founded_year", "founded_evidence"), ("managing_directors", "managing_directors_evidence"),
             ("heating_billing", "heating_billing_evidence"), ("portal_or_app", "portal_evidence")]
    for f, e in pairs:
        val, quote = facts.get(f), facts.get(e)
        empty = val in (None, [], "", "not_mentioned", "unclear")
        if empty: continue
        checked += 1
        parts = [q for q in re.split(r"\s*(?:\.\.\.|…|;|\|)\s*", quote or "") if len(norm_loose(q)) >= 4]
        ok = bool(parts) and all(norm(q) in nt or norm_loose(q) in nl for q in parts)
        # numbers must also appear inside their own quote
        if ok and isinstance(val, int) and f != "founded_year":
            digits = re.sub(r"\D", "", re.sub(r"(\d)[.\s](\d{3})", r"\1\2", quote))
            ok = str(val) in digits
        if ok and f == "founded_year": ok = str(val) in (quote or "")
        if not ok:
            failed += 1; facts[f + "_rejected"] = val
            facts[f] = [] if isinstance(val, list) else ("unclear" if f == "is_property_manager" else
                       ("not_mentioned" if f == "heating_billing" else None))
    facts["_evidence_checked"], facts["_evidence_failed"] = checked, failed
    return facts

ORDER = [("about", 3500), ("services", 2200), ("home", 2500), ("impressum", 1800), ("jobs", 900), ("portal", 300)]
def process(c, crawl, k, rl):
    f = LLM / f"{c['company_id']}.json"
    pages = {}
    for cat, url in crawl.get("pages", {}).items():
        p = cached_page(url)
        if p and p["html"]:
            t, _ = to_text(p["html"]); pages[cat] = {"url": url, "html": p["html"], "text": t}
    if not pages: return None
    sig = signals(pages)
    if f.exists():
        facts = json.loads(f.read_text(encoding="utf-8"))
    else:
        parts = [f"Company (from Google): {c['company_name']}, {c['address']}"]
        for cat, lim in ORDER:
            if cat in pages: parts.append(f"\n=== PAGE: {cat} ({pages[cat]['url']}) ===\n{pages[cat]['text'][:lim]}")
        facts, model = call_claude(k, "\n".join(parts), rl)
        facts = verify(facts, " ".join(p["text"] for p in pages.values()))
        facts["_model"] = model
        facts["_source_urls"] = {cat: p["url"] for cat, p in pages.items()}
        f.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"company_id": c["company_id"], **sig, **facts}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rpm", type=int, default=45, help="requests per minute to the Claude API")
    ap.add_argument("--workers", type=int, default=6); a = ap.parse_args()
    k = key(); rl = RateLimiter(a.rpm)
    crawl = {r["company_id"]: r for r in json.load(open(D / "crawl_index.json", encoding="utf-8"))}
    cos = [c for c in csv.DictReader(open(D / "companies_merged.csv", encoding="utf-8-sig")) if c["company_id"] in crawl and crawl[c["company_id"]].get("pages")]
    if a.limit: cos = cos[:a.limit]
    todo = sum(1 for c in cos if not (LLM / f"{c['company_id']}.json").exists())
    print(f"{len(cos)} companies with a reachable website, {todo} still need the LLM step (~{todo / a.rpm:.0f} min at {a.rpm}/min)")
    out, errors, t0 = [], [], time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(process, c, crawl[c["company_id"]], k, rl): c for c in cos}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
                if r: out.append(r)
            except Exception as e:
                errors.append((futs[fu]["company_id"], str(e)[:200]))
                if "credit balance" in str(e).lower():
                    print("\nAPI credit is used up. Top up at console.anthropic.com -> Billing, then run the same command again;"
                          " finished companies are cached and will not be paid for twice.")
                    import os; os._exit(1)
                if len(errors) == 3 and not out: sys.exit(f"Stopping - first errors: {errors}")
            if i % 25 == 0 or i == len(cos):
                cost = STATE["in"] / 1e6 * PRICE_IN + STATE["out"] / 1e6 * PRICE_OUT
                print(f"  {i}/{len(cos)} | {time.time()-t0:.0f}s | model {STATE['model']} | est. cost this run ${cost:.2f}")
    cols = sorted({k for r in out for k in r}, key=lambda x: (x.startswith("_"), x))
    with open(D / "kpis.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for r in out: w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    chk = sum(r.get("_evidence_checked", 0) for r in out); fail = sum(r.get("_evidence_failed", 0) for r in out)
    print(f"\nDone: {len(out)} companies, {len(errors)} errors")
    print(f"Evidence check: {chk} values checked, {fail} rejected ({fail / max(1, chk):.1%}) - rejected values were NOT used")
    print("Property manager:", Counter(r["is_property_manager"] for r in out))
    print("Units stated:", sum(1 for r in out if r.get("units_managed")), "| VDIV:", sum(r["vdiv_member"] == "yes" for r in out),
          "| software:", sum(bool(r["software_detected"]) for r in out), "| competitor named:", sum(bool(r["competitor_mentioned"]) for r in out))
    print(f"Tokens: {STATE['in']:,} in / {STATE['out']:,} out")
    if errors: json.dump(errors, open(D / "kpi_errors.json", "w"), indent=1); print("Errors saved to data/kpi_errors.json")

if __name__ == "__main__":
    main()
