# Berlin Property-Manager Lead Engine

An automated pipeline that finds every property management company (Hausverwaltung) in Berlin, checks each one on its own website, and ranks them as sales leads for a heat- and water-metering provider.

Built in about a day as a take-home case study for a B2B sales role in German proptech. Python (standard library only) plus Claude for text extraction. A full run costs about **$12**.

## Why this problem

Under the German Heating Cost Ordinance (Heizkostenverordnung), heat and water meters must be remotely readable by **31 December 2026**, and tenants then receive a monthly consumption report. Every property manager needs a metering partner for this, which makes them time-sensitive leads. The question is which of the ~2,000 firms in Berlin to call first.

## Results (run of 9 November 2026)

| Step | Result |
|---|---|
| Places collected (Google Places API) | 2,671 |
| Unique Berlin companies after de-duplication | 2,382 |
| Confirmed as property managers on their own website | 724 |
| Leads (confirmed + likely + no website) | 1,320 |
| A-leads | 93, with 134 named managing directors |
| AI values rejected by the evidence check | 3.5% of 3,529 |
| Re-check of 25 random companies | 96% right on "is it a property manager", 91% on grade, 71% on unit counts |

## How it works

```
Google Places API ──► clean & de-duplicate ──► crawl websites ──► extract KPIs ──► score ──► Google Sheets
  (quadtree search)     (domain/phone/name)     (6 key pages)    (regex + Claude)  (live formulas)
```

1. **Collect** (`collect_places.py`): Google returns at most 60 results per search, so one query misses most of the market. The script splits Berlin into a grid and recursively splits any cell that hits the cap (an adaptive quadtree), for 4 keywords. A saturation log shows coverage: the 1st keyword found 2,415 companies, the 4th only 15 new ones.
2. **Clean** (`clean_places.py`): Berlin-postcode filter, district filled from a postcode map learned from the data itself, and entity resolution by domain, phone and normalised name (branches are merged, not dropped). Rule-based pre-tiers; a company is excluded only with evidence.
3. **Cross-check** (`merge_sources.py`): a Gelbe Seiten sample estimates Google's coverage (~78%) and adds the companies Google missed.
4. **Crawl** (`crawl_websites.py`): homepage plus the best-matching Impressum, About, Services, Jobs and Portal pages. Everything is cached, so reruns are free and resumable.
5. **Extract** (`extract_kpis.py`):
   - **Deterministic signals** (regex, no AI): VDIV/IVD membership badge, certified manager, §34c licence, software in use (casavi, facilioo, immoware24, …), metering competitor named (ista, Techem, KALO, …), job ads.
   - **Claude** with a fixed JSON schema: is it really a property manager, services, units, staff, founding year, directors, how billing is handled.
   - **Evidence-grounded:** every value must come with a verbatim quote, and code checks the quote is really on the page. Values that fail are dropped.
6. **Score** (`build_leads.py`): Fit (70 points: portfolio size, management type, professional standing, digital readiness, reputation) + Timing (30 points: metering signal, hiring, growth). The score is written as live Google Sheets formulas, with the weights on a separate tab so they can be changed.
7. **Contacts** (`find_contacts.py`): managing directors from the legally required Impressum. Emails are labelled "published on website", "pattern-based (MX checked)" or "company inbox only"; no addresses are guessed beyond that.

### Design choices

- **Code first, LLM second.** Anything a regex can find never goes to the model: free, repeatable, no hallucination.
- **Evidence over trust.** Each AI value carries its quote; one company named "… Construction GmbH" ranked #2 because its website says *"Hausverwaltung seit 1984 · Einheiten verwaltet 11.806"*.
- **Pilot before spending.** A 20-company test run found a redirect bug and a too-strict quote check before the full run.
- **Honest estimates.** Only 12% of firms publish unit counts; the rest are estimated (staff × 143, then a review-count regression) and labelled as estimates.

## Setup

Requires Python 3.10+. No packages to install.

```bash
cp .env.example .env      # then add your keys
```

```
GOOGLE_PLACES_API_KEY=...   # Google Cloud, "Places API (New)" enabled
ANTHROPIC_API_KEY=...       # console.anthropic.com
```

## Run

```bash
python collect_places.py --test     # one cheap request to check the key
python collect_places.py            # full collection (budget cap: 900 requests)
python clean_places.py
python merge_sources.py             # optional: needs data/gelbeseiten_sample.json
python crawl_websites.py --limit 20 # test, then without --limit
python extract_kpis.py --limit 20   # test, then without --limit
python build_leads.py               # writes sheet_import/leads_final.csv
python find_contacts.py             # contacts for the A-leads
```

Import `sheet_import/leads_final.csv` into Google Sheets with "Convert text to numbers, dates and formulas" ticked; the score columns are formulas that reference a `Scoring` tab.

## Known limitations

- Unit counts are the weakest field (71% in the re-check): the model sometimes takes a subsidiary's figure, or files a number under the wrong field.
- Some large firms state Germany-wide figures, not Berlin.
- Firms without a website (482) are kept but graded C, because they can't be checked.

## Data and compliance

The `data/` and `sheet_import/` folders (cached websites, API responses, contact names and emails) are excluded from this repository on purpose. Collecting business contacts for B2B research relies on GDPR Art. 6(1)(f); under UWG §7, cold email in Germany needs prior consent, so the recommended first contact is a phone call or LinkedIn.

## Author

Hemant Kumar · [LinkedIn](https://www.linkedin.com/in/hemantk-dev/)
