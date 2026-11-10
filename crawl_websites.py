"""
Heidi case - Task 2, step 1: crawl each lead's website (homepage + the pages that matter).

For every company with a website we fetch the homepage and then pick, from its own links,
the best candidate for each of these pages:
  impressum  (legal notice: managing directors, HRB number, §34c licence - legally required in DE)
  about      (Über uns / Unternehmen / Team: size, founding year, units managed)
  services   (Leistungen / WEG-/Mietverwaltung: what they manage)
  jobs       (Karriere / Stellenangebote: growth & billing-pain signal)
  portal     (Mieter-/Eigentümerportal, App: digital maturity)
Raw HTML is kept (badges such as VDIV logos and software widgets live in HTML, not text),
everything is cached in data/web/, so the crawl is resumable and runs only once.

Usage:  python crawl_websites.py --limit 20     (test)
        python crawl_websites.py                (all)
Standard library only.
"""
import argparse, csv, gzip, hashlib, json, re, ssl, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

HERE = Path(__file__).resolve().parent
D = HERE / "data"; WEB = D / "web"; WEB.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CTX = ssl.create_default_context()

PAGE_RULES = {   # category -> (href/text pattern, priority words)
    "impressum": re.compile(r"impressum|imprint|legal[-_ ]?notice|anbieterkennzeichnung", re.I),
    "about":     re.compile(r"[uü]ber[-_ ]?uns|ueber[-_ ]?uns|about|unternehmen|wir[-_ ]?[uü]ber|team|philosophie|geschichte|wer[-_ ]wir", re.I),
    "services":  re.compile(r"leistung|service|hausverwaltung|weg[-_ ]?verwaltung|mietverwaltung|verwaltung|angebot|portfolio", re.I),
    "jobs":      re.compile(r"karriere|jobs?\b|stellen|career|bewerb|wir[-_ ]?suchen", re.I),
    "portal":    re.compile(r"portal|login|app\b|mieterbereich|eigent[uü]merbereich|kundenbereich|online[-_ ]?service", re.I),
}

class LinkText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.links, self.text, self._skip, self._a = [], [], 0, None
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self._skip += 1
        if tag == "a":
            self._a = [dict(attrs).get("href") or "", ""]; self.text.append(" ")
        if tag in ("p","div","br","li","h1","h2","h3","h4","tr","section","footer","td","header","nav"): self.text.append("\n")
        elif tag in ("span","a","button","label","strong","b","em"): self.text.append(" ")
    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip: self._skip -= 1
        if tag == "a" and self._a is not None:
            self.links.append(tuple(self._a)); self._a = None
    def handle_data(self, data):
        if self._skip: return
        self.text.append(data)
        if self._a is not None: self._a[1] += data

def to_text(html):
    p = LinkText()
    try: p.feed(html)
    except Exception: pass
    t = "".join(p.text).replace("\xad", "")
    t = re.sub(r"[ \t\r\f\v]+", " ", t); t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip(), p.links

def fetch(url, timeout=15):
    key = hashlib.sha1(url.encode()).hexdigest()[:20]; f = WEB / f"{key}.json.gz"
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes()).decode("utf-8"))
    out = {"url": url, "final_url": "", "status": 0, "html": "", "error": ""}
    tries = [url] + ([url.replace("https://", "http://", 1)] if url.startswith("https://") else [])
    for u in tries:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                raw = r.read(2_000_000)
                ctype = r.headers.get("Content-Type", "")
                if "html" not in ctype and "text" not in ctype and ctype:
                    out.update(status=r.status, final_url=r.geturl(), error=f"not html: {ctype[:40]}"); break
                cs = (re.search(r"charset=([\w-]+)", ctype) or re.search(rb'charset=["\']?([\w-]+)', raw[:3000]))
                cs = (cs.group(1).decode() if isinstance(cs.group(1), bytes) else cs.group(1)) if cs else "utf-8"
                try: html = raw.decode(cs, errors="replace")
                except LookupError: html = raw.decode("utf-8", errors="replace")
                out.update(status=r.status, final_url=r.geturl(), html=html, error=""); break
        except urllib.error.HTTPError as e:
            out.update(status=e.code, error=f"HTTP {e.code}")
            if e.code in (403, 404, 410): break
        except Exception as e:
            out["error"] = type(e).__name__ + ": " + str(e)[:120]
    f.write_bytes(gzip.compress(json.dumps(out, ensure_ascii=False).encode("utf-8")))
    return out

def same_site(a, b):
    ra = ".".join(urlparse(a).netloc.lower().split(".")[-2:]); rb = ".".join(urlparse(b).netloc.lower().split(".")[-2:])
    return ra == rb

def pick_pages(base_url, links):
    best = {}
    for href, txt in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")): continue
        full = urljoin(base_url, href.split("#")[0])
        if not full.startswith("http") or not same_site(full, base_url): continue
        if re.search(r"\.(pdf|jpe?g|png|zip|docx?)$", full, re.I): continue
        hay = f"{urlparse(full).path} {txt}"
        for cat, rx in PAGE_RULES.items():
            if rx.search(hay):
                score = (2 if rx.search(txt or "") else 0) + (2 if rx.search(urlparse(full).path) else 0) - full.count("/") * 0.1
                if cat not in best or score > best[cat][0]: best[cat] = (score, full)
    # one URL should not serve two categories unless it is the only option
    return {c: u for c, (s, u) in best.items()}

def crawl_company(c):
    site = c["website"]
    if not site.startswith("http"): site = "http://" + site
    root = f"{urlparse(site).scheme}://{urlparse(site).netloc}/"
    home_key = root
    home = fetch(root)
    if not home["html"] and site.rstrip("/") != root.rstrip("/"):
        home_key = site; home = fetch(site)
    res = {"company_id": c["company_id"], "root": root, "home_status": home["status"], "home_error": home["error"],
           "final_url": home["final_url"], "pages": {}}
    if not home["html"]: return res
    text, links = to_text(home["html"])
    res["pages"]["home"] = home_key          # the URL the page is cached under (final_url may differ after redirects)
    for cat, url in pick_pages(home["final_url"] or root, links).items():
        if url.rstrip("/") == (home["final_url"] or root).rstrip("/"): continue
        p = fetch(url)
        if p["html"]: res["pages"][cat] = url
        time.sleep(0.3)   # be polite to small company websites
    return res

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    cos = [c for c in csv.DictReader(open(D / "companies_merged.csv", encoding="utf-8-sig"))
           if c["tier"] != "excluded" and c["website"]]
    if a.limit: cos = cos[:a.limit]
    print(f"Crawling {len(cos)} websites with {a.workers} parallel workers...")
    results, t0 = [], time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(crawl_company, c): c for c in cos}
        for i, f in enumerate(as_completed(futs), 1):
            try: results.append(f.result())
            except Exception as e: results.append({"company_id": futs[f]["company_id"], "home_error": f"crash: {e}", "pages": {}})
            if i % 50 == 0 or i == len(cos):
                ok = sum(1 for r in results if r["pages"])
                print(f"  {i}/{len(cos)} done | reachable {ok} | {time.time()-t0:.0f}s")
    json.dump(results, open(D / "crawl_index.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = [r for r in results if r["pages"]]
    from collections import Counter
    cats = Counter(k for r in ok for k in r["pages"])
    print(f"\nDone. Reachable websites: {len(ok)}/{len(results)}")
    print("Pages found:", dict(cats))
    print("File: data/crawl_index.json (pages cached in data/web/)")

if __name__ == "__main__":
    main()
