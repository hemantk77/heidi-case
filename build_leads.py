"""
Heidi case - Task 2, step 3: combine collection + website KPIs into the final lead table.

Qualification STATUS (data-driven, before any scoring):
  Qualified        website confirms property management (or own housing portfolio)
  Likely           strong name/category signal, website does not say clearly
  Unverified       no reachable website - kept on name/category evidence only
  Not a fit        website shows another business (pure brokerage, investment, caretaking...)
                   -> moved to the Disqualified tab with the quote that shows it
The SCORE itself is computed in Google Sheets with formulas over these columns, using the
weights on the Scoring tab, so Heidi can change a weight and the ranking updates.
"""
import csv, json, re, sys, math
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
D = Path(__file__).resolve().parent / "data"; OUT = Path(__file__).resolve().parent / "sheet_import"

C = list(csv.DictReader(open(D / "companies_merged.csv", encoding="utf-8-sig")))
K = {r["company_id"]: r for r in csv.DictReader(open(D / "kpis.csv", encoding="utf-8-sig"))}

def jl(s):
    try: v = json.loads(s) if s else []
    except Exception: v = [s]
    return v if isinstance(v, list) else [v]
def yn(b): return "yes" if b else "no"
def clean(s, n=250): return re.sub(r"\s+", " ", s or "").strip()[:n]

leads, disq = [], []
for c in C:
    if c["tier"] == "excluded":
        disq.append({**c, "dq_reason": c["tier_reason"], "dq_evidence": ""}); continue
    k = K.get(c["company_id"])
    seg = {"cooperative": "Housing cooperative", "large_landlord": "Large landlord"}.get(c["tier"], "Property manager")
    if k:
        pm, mb = k["is_property_manager"], k["main_business"]
        services = [s for s in jl(k["services"])]
        weg = "WEG-Verwaltung" in services; miet = "Mietverwaltung" in services or mb == "housing_owner_landlord"
        if pm == "yes" or (pm == "unclear" and mb in ("property_management", "housing_owner_landlord")):
            status = "Qualified" if pm == "yes" else "Likely"
        elif pm == "unclear":
            status = "Likely" if c["tier"] in ("core", "cooperative", "large_landlord") else "Not a fit"
        else:
            status = "Not a fit"
        if status == "Not a fit":
            disq.append({**c, "dq_reason": f"website shows main business: {mb.replace('_', ' ')}",
                         "dq_evidence": clean(k.get("is_property_manager_evidence"), 200)}); continue
    else:
        if c["tier"] == "check":
            why = "no website could be read" if c["website"] else "no website"
            disq.append({**c, "dq_reason": f"not verifiable: {why}, and the name does not indicate property management", "dq_evidence": ""}); continue
        status = "Unverified"
        services, weg, miet = [], False, False
    k = k or {}
    mds = [m for m in jl(k.get("managing_directors")) if m]
    ev = []
    if k.get("units_managed"): ev.append(f"Units: “{clean(k.get('units_evidence'), 160)}”")
    if k.get("employees"): ev.append(f"Staff: “{clean(k.get('employees_evidence'), 120)}”")
    if k.get("heating_billing") not in (None, "", "not_mentioned"): ev.append(f"Billing: “{clean(k.get('heating_billing_evidence'), 160)}”")
    if k.get("is_property_manager") == "yes": ev.append(f"Business: “{clean(k.get('is_property_manager_evidence'), 140)}”")
    urls = json.loads(k["_source_urls"]) if k.get("_source_urls") else {}
    comp = "|".join(x for x in (k.get("competitor_mentioned") or "").split("|") if x)
    fields_known = sum(bool(x) for x in [k.get("units_managed"), k.get("employees"), k.get("founded_year"), mds,
                       services, k.get("portal_or_app") or k.get("software_detected"), k.get("heating_billing") not in (None, "", "not_mentioned")])
    leads.append({
        "ID": c["company_id"], "Company": c["company_name"], "Status": status, "Segment": seg,
        "District": c["bezirk"], "Address": c["address"], "Website": c["website"], "Phone": c["phone"],
        "Units stated": k.get("units_managed") or "", "Employees stated": k.get("employees") or "",
        "WEG management": yn(weg), "Rental management": yn(miet),
        "Services": ", ".join(s for s in services if s != "Other"),
        "VDIV member": k.get("vdiv_member", "no"), "IVD member": k.get("ivd_member", "no"),
        "Certified manager": k.get("certified_manager", "no"), "§34c licence": k.get("licence_34c", "no"),
        "Software detected": (k.get("software_detected") or "").replace("|", ", "),
        "Portal or app": clean(k.get("portal_or_app"), 60), "Portal link on site": k.get("has_portal_link", "no"),
        "Heating-cost billing": {"offers_as_service": "does it in-house", "mentions_external_provider": "external provider named",
                                 "not_mentioned": ""}.get(k.get("heating_billing", ""), ""),
        "Metering provider named": comp.replace("|", ", "),
        "Hiring now": k.get("hiring_now", "no"), "Hiring billing/accounting": k.get("hiring_billing_roles", "no"),
        "Google rating": c["google_rating"], "Google reviews": c["google_reviews"] or 0,
        "Berlin locations": c["berlin_locations"] or 1, "Founded": k.get("founded_year") or "",
        "Managing directors": ", ".join(mds)[:120], "Emails on website": (k.get("emails_on_site") or "").replace("|", ", "),
        "HRB": k.get("hrb", ""), "Description": clean(k.get("segment_note"), 200),
        "Data completeness": round(fields_known / 7, 2),
        "Evidence (verbatim quotes)": " · ".join(ev)[:480],
        "Pages read": " ".join(urls.get(x, "") for x in ("home", "about", "services", "impressum") if urls.get(x)),
        "Google Maps": c["maps_url"], "Source": c["source"] + (" + company website" if k else ""),
    })

# ---- simulate the sheet formulas (same logic, used only to pick sensible grade thresholds)
P = dict(u0=100, u1=20000, upe=143, a=2.661, b=0.548, rev_full=200)
W = dict(size=30, type=15, standing=10, digital=10, rep=5, meter=15, growth=10, young=5)
def score(r):
    if r["Units stated"]: u = float(r["Units stated"])
    elif r["Employees stated"]: u = float(r["Employees stated"]) * P["upe"]
    else: u = 10 ** (P["a"] + P["b"] * math.log10(float(r["Google reviews"]) + 1))
    size = min(1, max(0, math.log10(u / P["u0"]) / math.log10(P["u1"] / P["u0"])))
    typ = 1 if r["Segment"] != "Property manager" or (r["WEG management"] == "yes" and r["Rental management"] == "yes") else (0.8 if "yes" in (r["WEG management"], r["Rental management"]) else 0.4)
    nst = sum(r[x] == "yes" for x in ("VDIV member", "IVD member", "Certified manager", "§34c licence")); st = min(1, nst / 2)
    dig = 1 if (r["Software detected"] or r["Portal or app"]) else (0.6 if r["Portal link on site"] == "yes" else 0.2)
    rep = min(1, math.log10(float(r["Google reviews"]) + 1) / math.log10(P["rev_full"] + 1))
    met = 1 if r["Metering provider named"] else (0.8 if r["Heating-cost billing"] else 0.2)
    gro = 1 if r["Hiring billing/accounting"] == "yes" else (0.5 if r["Hiring now"] == "yes" else 0)
    yng = 1 if (int(r["Berlin locations"]) >= 2 or (r["Founded"] and int(r["Founded"]) >= 2015)) else 0
    return W["size"]*size + W["type"]*typ + W["standing"]*st + W["digital"]*dig + W["rep"]*rep + W["meter"]*met + W["growth"]*gro + W["young"]*yng
sc = sorted((score(r) for r in leads if r["Status"] in ("Qualified", "Likely")), reverse=True)
print("score quantiles (qualified+likely): top10%", round(sc[len(sc)//10]), "| top40%", round(sc[int(len(sc)*.4)]), "| median", round(sc[len(sc)//2]), "| max", round(sc[0]))
print("n >= 60:", sum(s >= 60 for s in sc), "| n >= 45:", sum(s >= 45 for s in sc))

order = {"Qualified": 0, "Likely": 1, "Unverified": 2}
leads.sort(key=lambda r: (order[r["Status"]], -score(r)))
for f, rows, cols in (("leads_v2.csv", leads, list(leads[0].keys())), ):
    with open(OUT / f, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for r in rows:
            r = {k: ("'" + v if isinstance(v, str) and v and v[0] in "=+-@" and k not in ("Phone",) else v) for k, v in r.items()}
            w.writerow(r)
dcols = [("company_id", "ID"), ("company_name", "Company"), ("dq_reason", "Reason"), ("dq_evidence", "Evidence (verbatim quote)"),
         ("bezirk", "District"), ("address", "Address"), ("website", "Website"), ("phone", "Phone"), ("google_reviews", "Google reviews"), ("source", "Source")]
with open(OUT / "disqualified_v2.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow([b for a, b in dcols])
    for r in disq: w.writerow([r.get(a, "") for a, b in dcols])
from collections import Counter
print("Leads:", len(leads), Counter(r["Status"] for r in leads), "| segments", Counter(r["Segment"] for r in leads))
print("Disqualified:", len(disq), Counter(r["dq_reason"].split(":")[0] for r in disq))
print("columns:", len(leads[0]), list(leads[0].keys()))

# ---------------------------------------------------------------- sheet version with live formulas
# Column layout of the Leads tab (letters are used in the formulas below)
LAYOUT = ["ID", "Company", "Grade", "Score", "Fit (of 70)", "Timing (of 30)", "Status", "Segment", "District", "Address",
    "Website", "Phone", "Units stated", "Employees stated", "Units used", "Units basis", "WEG management", "Rental management",
    "Services", "VDIV member", "IVD member", "Certified manager", "§34c licence", "Software detected", "Portal or app",
    "Portal link on site", "Heating-cost billing", "Metering provider named", "Hiring now", "Hiring billing/accounting",
    "Google rating", "Google reviews", "Berlin locations", "Founded", "Managing directors", "Emails on website", "HRB",
    "Description", "Data completeness", "Evidence (verbatim quotes)", "Pages read", "Google Maps", "Source",
    "Pts size", "Pts management type", "Pts standing", "Pts digital", "Pts reputation", "Pts metering signal", "Pts hiring", "Pts young/expanding"]
def col(name):
    i = LAYOUT.index(name) + 1; s = ""
    while i: i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s
S = "Scoring!$C$"
def formulas(n):
    L = lambda name: f"{col(name)}{n}"
    return {
        "Units used": f'=IF({L("Units stated")}<>"",{L("Units stated")},IF({L("Employees stated")}<>"",{L("Employees stated")}*{S}17,ROUND(10^({S}18+{S}19*LOG10({L("Google reviews")}+1)),-1)))',
        "Units basis": f'=IF({L("Units stated")}<>"","stated on website",IF({L("Employees stated")}<>"","estimated from staff","estimated from reviews"))',
        "Pts size": f'={S}4*MAX(0,MIN(1,LOG10({L("Units used")}/{S}15)/LOG10({S}16/{S}15)))',
        "Pts management type": f'={S}5*IF({L("Segment")}<>"Property manager",1,IF(AND({L("WEG management")}="yes",{L("Rental management")}="yes"),1,IF(OR({L("WEG management")}="yes",{L("Rental management")}="yes"),0.8,0.4)))',
        "Pts standing": f'={S}6*MIN(1,COUNTIF({L("VDIV member")}:{L("§34c licence")},"yes")/2)',
        "Pts digital": f'={S}7*IF(OR({L("Software detected")}<>"",{L("Portal or app")}<>""),1,IF({L("Portal link on site")}="yes",0.6,0.2))',
        "Pts reputation": f'={S}8*MIN(1,LOG10({L("Google reviews")}+1)/LOG10({S}20+1))',
        "Pts metering signal": f'={S}9*IF({L("Metering provider named")}<>"",1,IF({L("Heating-cost billing")}<>"",0.8,0.2))',
        "Pts hiring": f'={S}10*IF({L("Hiring billing/accounting")}="yes",1,IF({L("Hiring now")}="yes",0.5,0))',
        "Pts young/expanding": f'={S}11*IF(OR({L("Berlin locations")}>=2,AND({L("Founded")}<>"",{L("Founded")}>=2015)),1,0)',
        "Fit (of 70)": f'=ROUND(SUM({L("Pts size")}:{L("Pts reputation")}),1)',
        "Timing (of 30)": f'=ROUND(SUM({L("Pts metering signal")}:{L("Pts young/expanding")}),1)',
        "Score": f'=ROUND({L("Fit (of 70)")}+{L("Timing (of 30)")},0)',
        "Grade": f'=IF({L("Status")}="Unverified","C",IF({L("Score")}>={S}21,"A",IF({L("Score")}>={S}22,"B","C")))',
    }
with open(OUT / "leads_final.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(LAYOUT)
    for n, r in enumerate(leads, start=2):
        f = formulas(n); row = []
        for name in LAYOUT:
            if name in f: row.append(f[name]); continue
            v = r.get(name, "")
            if isinstance(v, str) and v and v[0] in "=+-@": v = "'" + v       # keep phone numbers / odd names as text
            row.append(v)
        w.writerow(row)
print("leads_final.csv written:", len(leads), "rows,", len(LAYOUT), "columns; Grade =", col("Grade"), "Score =", col("Score"))
