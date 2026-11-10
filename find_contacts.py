"""
Heidi case - Bonus: contact persons for the A-leads.

Who:   managing directors (Geschäftsführer / Inhaber / Vorstand) as named in the Impressum,
       which German law (§5 DDG) requires every business website to publish.
Email: only two kinds, clearly labelled -
         'published on website'  the address appears on the company's own site
         'pattern-based'         built from the company's own published pattern
                                 (e.g. ramona.benzler@ -> firstname.lastname@), domain has MX records
         'generic only'          only info@/kontakt@ published - no personal address guessed
       No SMTP probing, no third-party data brokers.
LinkedIn: a people-search link (name + company), to be confirmed by hand - profiles are not scraped.
Hook:  one line built from this lead's own data, for the first conversation.

Usage: python find_contacts.py
"""
import csv, json, math, re, subprocess, sys, unicodedata
from pathlib import Path
from urllib.parse import quote, urlparse
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE = Path(__file__).resolve().parent; D = HERE / "data"; OUT = HERE / "sheet_import"
sys.path.insert(0, str(HERE))

# --- A-leads: same rule as the sheet (score >= 65, not unverified)
P = dict(u0=100, u1=20000, upe=143, a=2.661, b=0.548, rev_full=200)
W = dict(size=30, type=15, standing=10, digital=10, rep=5, meter=15, growth=10, young=5)
def score(r):
    if r["Units stated"]: u = float(r["Units stated"])
    elif r["Employees stated"]: u = float(r["Employees stated"]) * P["upe"]
    else: u = 10 ** (P["a"] + P["b"] * math.log10(float(r["Google reviews"]) + 1))
    size = min(1, max(0, math.log10(u / P["u0"]) / math.log10(P["u1"] / P["u0"])))
    typ = 1 if r["Segment"] != "Property manager" or (r["WEG management"] == "yes" and r["Rental management"] == "yes") else (0.8 if "yes" in (r["WEG management"], r["Rental management"]) else 0.4)
    st = min(1, sum(r[x] == "yes" for x in ("VDIV member", "IVD member", "Certified manager", "§34c licence")) / 2)
    dig = 1 if (r["Software detected"] or r["Portal or app"]) else (0.6 if r["Portal link on site"] == "yes" else 0.2)
    rep = min(1, math.log10(float(r["Google reviews"]) + 1) / math.log10(P["rev_full"] + 1))
    met = 1 if r["Metering provider named"] else (0.8 if r["Heating-cost billing"] else 0.2)
    gro = 1 if r["Hiring billing/accounting"] == "yes" else (0.5 if r["Hiring now"] == "yes" else 0)
    yng = 1 if (int(r["Berlin locations"]) >= 2 or (r["Founded"] and int(r["Founded"]) >= 2015)) else 0
    return round(W["size"]*size + W["type"]*typ + W["standing"]*st + W["digital"]*dig + W["rep"]*rep + W["meter"]*met + W["growth"]*gro + W["young"]*yng), u

leads = list(csv.DictReader(open(OUT / "leads_v2.csv", encoding="utf-8")))
A = []
for r in leads:
    s, u = score(r)
    if r["Status"] != "Unverified" and s >= 65: A.append((s, u, r))
A.sort(key=lambda x: -x[0])
print(f"A-leads: {len(A)}")

# --- names and email patterns
TITLES = r"\b(dr|prof|dipl|ing|kfm|kffr|betriebsw|mba|ll\.?m|rechtsanw\w*|herr|frau|ra|b\.?\s?a|m\.?\s?sc|fh|vwl|bwl)\.?(-\w+\.?)?\s*"
def split_name(full):
    n = re.sub(r"\(.*?\)", "", full); n = re.sub(TITLES, "", n, flags=re.I)
    n = re.sub(r"[^\w\s'-]", " ", n); parts = [p for p in n.split() if len(p) > 1]
    if len(parts) < 2: return None
    return parts[0], parts[-1]
def ascii_de(s):
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
GENERIC = re.compile(r"^(info|kontakt|contact|office|mail|post|service|verwaltung|hausverwaltung|buero|büro|empfang|zentrale|datenschutz|"
                     r"bewerbung|jobs?|karriere|hr|personal|vermietung|weg|mieter|rechnung|buchhaltung|technik|notfall|support|hello|hallo|team|admin|webmaster|presse)\b", re.I)
def pattern_of(local):
    if GENERIC.match(local): return None
    m = re.fullmatch(r"([a-z]+)[.\-_]([a-z]+)", local)
    if m: return "f.last" if len(m.group(1)) == 1 else "first.last"
    return None
def build(pat, first, last):
    f, l = ascii_de(first), ascii_de(last)
    return {"first.last": f"{f}.{l}", "f.last": f"{f[0]}.{l}"}[pat]

MX = {}
def has_mx(domain):
    if domain in MX: return MX[domain]
    ok = None
    try:
        out = subprocess.run(["nslookup", "-type=mx", domain], capture_output=True, text=True, timeout=10).stdout.lower()
        ok = "mail exchanger" in out or "mx preference" in out
    except Exception:
        ok = None
    MX[domain] = ok; return ok

def hook(r, u, basis):
    bits = []
    if r["Metering provider named"]: bits.append(f"names {r['Metering provider named']} as metering provider: switching window before the 31.12.2026 remote-reading deadline")
    elif r["Heating-cost billing"]: bits.append("does heating-cost billing in-house: needs monthly meter data for UVI from 2027")
    if r["Software detected"]: bits.append(f"runs {r['Software detected']}: data can flow straight into their tool")
    elif r["Portal or app"]: bits.append(f"offers a portal/app ({r['Portal or app']}): monthly consumption info can go there")
    if r["Hiring billing/accounting"] == "yes": bits.append("hiring for billing/accounting right now")
    if r["VDIV member"] == "yes": bits.append("VDIV member (Heidi's partner association)")
    size = f"~{int(u):,} units ({basis})" if u else ""
    return (size + "; " if size else "") + "; ".join(bits[:3])

from extract_kpis import cached_page
CRAWL = {c["company_id"]: c for c in json.load(open(D / "crawl_index.json", encoding="utf-8"))}
EM = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
OBF = re.compile(r"([\w.+-]+)\s*(?:\(at\)|\[at\]|\{at\}| at |\(ät\)|\[ät\]|\s@\s)\s*([\w-]+)\s*(?:\(dot\)|\[dot\]|\(punkt\)|\[punkt\]|\.| dot | punkt )\s*([a-z]{2,6})\b", re.I)
def all_emails(cid):
    found = []
    for url in CRAWL.get(cid, {}).get("pages", {}).values():
        p = cached_page(url)
        if not p or not p["html"]: continue
        h = p["html"]
        found += re.findall(r'mailto:([^"\'?>\s]+)', h, re.I)
        found += EM.findall(re.sub(r"<[^>]+>", " ", h))
        found += ["%s@%s.%s" % m for m in OBF.findall(re.sub(r"<[^>]+>", " ", h))]
    clean = []
    for e in found:
        e = e.strip(".,;:").lower()
        if re.search(r"\.(png|jpe?g|gif|webp|svg|css|js)$|example|sentry|wixpress|domain\.|ihre-?mail|beispiel|@2x", e): continue
        if e not in clean: clean.append(e)
    return clean

rows = []
for s, u, r in A:
    dom = urlparse(r["Website"] if "//" in r["Website"] else "http://" + r["Website"]).netloc.lower()
    dom = re.sub(r"^www\d?\.", "", dom)
    emails = all_emails(r["ID"]) or [e.strip() for e in r["Emails on website"].split(",") if e.strip()]
    own = [e for e in emails if e.split("@")[-1].endswith(dom.split(".", 1)[-1] if dom.count(".") > 1 else dom)] or emails
    generic = next((e for e in own if GENERIC.match(e.split("@")[0])), "")
    pats = [pattern_of(e.split("@")[0]) for e in own]; pat = next((p for p in pats if p), None)
    mx = has_mx(dom) if dom else None
    mds = [m.strip() for m in r["Managing directors"].split(",") if m.strip()] or [""]
    basis = "stated" if r["Units stated"] else "estimated"
    for i, md in enumerate(mds[:2]):          # at most two people per company
        nm = split_name(md) if md else None
        email, status = "", ""
        if nm:
            f, l = ascii_de(nm[0]), ascii_de(nm[1])
            pub = next((e for e in own if l in ascii_de(e.split("@")[0]) and (f in ascii_de(e) or ascii_de(e.split("@")[0]).startswith(f[0]))), "")
            if pub: email, status = pub, "published on website"
            elif pat and mx: email, status = f"{build(pat, *nm)}@{dom}", f"pattern-based ({pat}@, MX ok)"
        if not email and generic: email, status = generic, "generic only (company inbox)"
        if not email: status = "none found"
        rows.append({
            "ID": r["ID"], "Company": r["Company"], "Score": s, "Segment": r["Segment"],
            "Contact": md or "(not named in Impressum)", "Role": "Managing director / owner (Impressum)" if md else "",
            "Email": email, "Email status": status,
            "Phone (company)": r["Phone"], "Website": r["Website"],
            "LinkedIn": f"https://www.linkedin.com/search/results/people/?keywords={quote(md + ' ' + re.sub(r' GmbH.*| AG.*| KG.*', '', r['Company']))}" if md else "",
            "LinkedIn status": "search link, confirm profile by hand" if md else "",
            "Why now (talking point)": hook(r, u, basis),
            "Note": ("Enterprise account: route via procurement / Head of operating costs, not the board" if r["Segment"] != "Property manager" else ""),
        })

with open(OUT / "contacts.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
from collections import Counter
print(f"Contacts: {len(rows)} for {len(A)} companies")
print("Email status:", Counter(r["Email status"].split(" (")[0] for r in rows))
print("MX lookups:", Counter(str(v) for v in MX.values()))
print("File: sheet_import/contacts.csv")
