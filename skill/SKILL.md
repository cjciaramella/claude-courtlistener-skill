---
name: court-listener
description: Use when searching federal court cases, retrieving dockets or court orders, working with the CourtListener REST API, downloading PACER/RECAP documents, or processing CourtListener docket CSV exports.
---

# CourtListener

## Overview

CourtListener (courtlistener.com) is a free archive of US federal court records backed by the RECAP project. It exposes a paginated REST API for searching dockets, reading docket entries, and downloading PDFs. This skill captures the API conventions that are **not obvious from the docs** and cost real debugging time to learn.

## When to use

- "Search for a federal court case" / "pull the docket for X"
- Retrieving court orders, opinions, filings, or PDFs by case number
- Writing any code that hits `courtlistener.com/api/rest/`
- Processing a CourtListener docket CSV (the UI export with `recapdocument_*` columns)
- Working with PACER data via the RECAP archive

## Authentication

Get a token at https://www.courtlistener.com/profile/api/. Pass it as:

```python
headers = {"Authorization": f"Token {api_token}"}
```

Store in `COURTLISTENER_API_TOKEN` env var; never hardcode.

## Dependencies

This project has no `requirements.txt`. The two packages actually used are `requests` and `pandas` — install them before running any script:

```bash
pip install requests pandas
```

## The non-obvious API rules

These all have bit past code. Follow them without exception.

### 1. Use v4, not v3

Base URL must be `https://www.courtlistener.com/api/rest/v4/`. v3 is deprecated and some endpoints return incomplete data silently.

### 2. Prefer the search endpoint for enumerating a docket's documents

To find all RECAP documents for a known docket ID, the best-working path is:

```
GET /api/rest/v4/search/?type=rd&q=docket_id:{docket_id}
```

Each result contains `filepath_local`, `is_available`, `entry_number`, `entry_date_filed`, `short_description` — enough to pick the "latest filing" or filter by date in a single paginated pass. This is one call per page instead of entries-then-per-document fetching.

The **docket-entries** endpoint (`GET /api/rest/v4/docket-entries/?docket={id}`) is also documented and sometimes used, but has returned **403 Forbidden** in testing for at least one valid token — treat it as a fallback, not the primary path.

### 2a. Search API has two type codes — pick the right one

The `/search/` endpoint's `type` parameter changes what each result represents:

| `type` | Results are | Use for |
|--------|-------------|---------|
| `r` | RECAP **dockets** (cases) | Finding cases by keyword/cause — "recent civil rights suits," "cases mentioning X" |
| `rd` | RECAP **documents** (filings) | Enumerating all filings in a known docket, or finding documents by text |

Example — find recent Section 1983 cases mentioning a topic:

```python
GET /api/rest/v4/search/?type=r&q=civil+rights+1983&order_by=dateFiled+desc
```

Each `type=r` hit has `caseName`, `docketNumber`, `dateFiled`, `court`, `cause`, `absolute_url`. Use these for case-discovery workflows; use `type=rd` when you already know the docket and want its filings.

### 3. Docket entries are NOT embedded in a docket response

`GET /api/rest/v4/dockets/{id}/` does **not** return docket entries, no matter what the response object shape suggests. Use the search endpoint above, or the `/docket-entries/?docket={id}` endpoint as a fallback.

### 4. `filepath_local` may be a relative path — prefix it

`filepath_local` often comes back as `recap/gov.uscourts.flmd.446179/gov.uscourts.flmd.446179.259.0.pdf` (relative) instead of a full URL. Normalize before fetching:

```python
pdf_url = doc["filepath_local"]
if not pdf_url.startswith("http"):
    pdf_url = "https://storage.courtlistener.com/" + pdf_url.lstrip("/")
```

### 5. Check `is_available` before downloading

Sealed, restricted, or not-yet-uploaded docs will 403 or 404 if you try to fetch `filepath_local` anyway. Always gate the download on `is_available is True`.

### 6. If you use `/docket-entries/`, RECAP documents inside are URLs, not objects

`entry["recap_documents"]` is a list of URLs. Each must be fetched separately (`GET /api/rest/v4/recap-documents/{id}/`) to get `filepath_local` and `is_available`. The search endpoint in rule 2 returns these inline, so you skip this step entirely.

### 7. Responses are paginated — follow `next`

Collections return `{"results": [...], "next": "url_or_null"}`. Loop until `next` is null. Search over a 400+ entry docket can span 20+ pages.

### 8. `order_by` syntax depends on the endpoint

- **Search API (`/search/`)**: use `order_by=dateFiled desc` (camelCase field, space-separated direction). This works.
- **Dockets API (`/dockets/`)**: use `order_by=-date_filed` (snake_case, leading `-` for descending). This works.
- **Docket-entries / recap-documents endpoints**: `order_by=-date_filed` returns **403**. Sort client-side.

### 9. Field names differ between the Search API and the Dockets API

Same data, different casing — trips up any code that handles results from both:

| Search API (`/search/`) | Dockets API (`/dockets/`) |
|-------------------------|---------------------------|
| `caseName` | `case_name` |
| `docketNumber` | `docket_number` |
| `dateFiled` | `date_filed` |
| `suitNature` | `nature_of_suit` |
| `court` | `court_id` |

If you write code that consumes results from both endpoints, normalize to one shape at the boundary.

### 10. Search API pagination uses cursor, not page

`/search/` returns `next` as a URL with a `cursor=...` query parameter. Either follow `next` verbatim (easiest) or extract the cursor:

```python
import re
cursor = re.search(r"cursor=([^&]+)", data["next"]).group(1) if data.get("next") else None
```

Other collection endpoints (`/dockets/`, `/docket-entries/`) use standard `page=` pagination.

## Rate limiting

Authenticated users get 5 req/s. Be conservative:

- `time.sleep(0.25)` between search/metadata calls (4 req/s)
- `time.sleep(0.5)` between PDF downloads (2 req/s)

Exceeding the limit returns HTTP 429. There is no auto-retry in the API — handle it yourself.

## Case number normalization

CourtListener docket numbers have the form `{office}:{YY}-{cv|cr}-{NNNNN}` where `office` is a single digit indicating the division within the district (0, 1, 2, 3, ...). The office digit **varies by case** — it's not always 0.

| Input | CourtListener format | Notes |
|-------|----------------------|-------|
| `26-CV-0107` (office unknown) | `0:26-cv-00107` | Guess `0:` only if the user didn't supply one |
| `2:25-cv-00747` | `2:25-cv-00747` | Already in CL format — pass through |
| `25-CR-610` | `0:25-cr-00610` | Zero-pad number to 5 digits |

Rules when normalizing:

1. If the user's input **already has** an `{N}:` prefix, preserve it verbatim — don't rewrite to `0:`.
2. If the user's input has no prefix, `0:` is the most common guess but can miss cases in multi-division districts. If the first search returns zero hits, try without the prefix (`docket_number=25-cv-00107`) or search by case name.
3. Zero-pad the trailing number to 5 digits.
4. Lowercase `cv` / `cr`.

**Do not** blindly rewrite a supplied prefix. The habeas scripts in this repo hardcode `0:` because they only handle one district — that's not a general-purpose pattern.

## Court codes

Most-used federal district codes (full list: `GET /api/rest/v4/courts/`):

| Code | Court |
|------|-------|
| `mnd` | Minnesota District |
| `ilnd` | Northern District of Illinois |
| `nysd` | Southern District of New York |
| `cacd` | Central District of California |
| `dcd` | District of Columbia |

## End-to-end workflow: case number → PDF (preferred, search-based)

This is the path that actually works across tokens and scales to long dockets.

```python
import os, time, requests

API = "https://www.courtlistener.com/api/rest/v4"
STORAGE = "https://storage.courtlistener.com/"
headers = {"Authorization": f"Token {os.environ['COURTLISTENER_API_TOKEN']}"}

# 1. Find the docket (preserve the user's office prefix verbatim if supplied).
r = requests.get(f"{API}/dockets/",
    params={"docket_number": "2:25-cv-00747", "court": "flmd"},
    headers=headers, timeout=30)
r.raise_for_status()
docket = r.json()["results"][0]
docket_id = docket["id"]
time.sleep(0.25)

# 2. Enumerate all RECAP documents for this docket via the search index.
#    Each result has filepath_local, is_available, entry_number, entry_date_filed.
docs, url = [], f"{API}/search/?type=rd&q=docket_id:{docket_id}"
while url:
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    docs.extend(data["results"])
    url = data.get("next")
    time.sleep(0.25)

# 3. Pick the latest AVAILABLE document (sort client-side; server-side order_by 403s).
available = [d for d in docs if d.get("is_available")]
latest = max(available, key=lambda d: (d["entry_date_filed"], d.get("entry_number") or 0))

# 4. Build the PDF URL (filepath_local is sometimes relative).
pdf_path = latest["filepath_local"]
pdf_url = pdf_path if pdf_path.startswith("http") else STORAGE + pdf_path.lstrip("/")

# 5. Download.
pdf = requests.get(pdf_url, headers=headers, timeout=60)
pdf.raise_for_status()
with open(f"doc_{latest['entry_number']}.pdf", "wb") as f:
    f.write(pdf.content)
```

### Fallback: `/docket-entries/` path (only if search is unavailable)

If for some reason the search endpoint doesn't fit (e.g., you need fields only on the docket-entries object), the older path is:

```
GET /api/rest/v4/docket-entries/?docket={docket_id}  # paginated, may 403
  → for entry in results:
      for recap_url in entry["recap_documents"]:     # URLs, not objects
          GET {recap_url}                            # returns filepath_local + is_available
```

Don't reach for this first — it's 2+ API calls per document instead of 1 per page.

## Working reference scripts live in this Github repo

At `https://github.com/cjciaramella/claude-courtlistener-skill/tree/master/examples` — read them for full error-handling, logging, and edge-case patterns before writing new code from scratch:

- `https://github.com/cjciaramella/claude-courtlistener-skill/blob/5e7ef3b1f4813c4bb0d96d89e71beb16a4cab95a/examples/courtlistener_habeas_searcher.py` — batch case search by docket number, CSV in/out, docket-number normalization
- `https://github.com/cjciaramella/claude-courtlistener-skill/blob/5e7ef3b1f4813c4bb0d96d89e71beb16a4cab95a/examples/courtlistener_order_downloader.py` — full search → entries → RECAP → PDF pipeline with pagination and rate limiting
- `https://github.com/cjciaramella/claude-courtlistener-skill/blob/5e7ef3b1f4813c4bb0d96d89e71beb16a4cab95a/examples/court_docket_processor.py` — processes a docket CSV **exported from the CourtListener UI** (has pre-populated `recapdocument_filepath_local` columns — no API calls needed for PDFs). Different data source than the scripts above.
- `https://github.com/cjciaramella/claude-courtlistener-skill/blob/5e7ef3b1f4813c4bb0d96d89e71beb16a4cab95a/examples/section_1983_tracker.py` — keyword-search case discovery via `/search/?type=r`. Topical/cause-of-action filtering (example: Section 1983 civil rights), cursor-based pagination, relevance filtering by keyword list. Use as the template for "find recent cases matching topic X" tasks.

Adapt these rather than rewriting. The duplication between them is intentional (they were written as standalone tools).

## Two CSV shapes — don't confuse them

1. **User-authored case list** (e.g., `minnesota_habeas_cases.csv`): columns like `case_number`, `petitioner_name`, `order_dates`. Requires API calls to find anything. This is what `courtlistener_habeas_searcher.py` consumes.
2. **CourtListener docket CSV export**: one row per document, with columns like `docketentry_description`, `docketentry_date_filed`, `recapdocument_document_number`, `recapdocument_description`, `recapdocument_is_available`, `recapdocument_filepath_local`, `recapdocument_page_count`, `recapdocument_document_type`, `recapdocument_attachment_number`. Downloaded from the UI's "Export" button, or produced programmatically (see below). This is what `court_docket_processor.py` consumes.

Ask which one the user has before writing code.

## Producing a docket CSV from the API (inverse of the UI export)

When the user asks for "a CSV of the docket" or "export this docket as CSV," you need to mimic the UI's export shape. Use the search endpoint from rule 2 above — each hit already contains the fields the processor reads. Mapping:

| CSV column | API field (from `/search/?type=rd&q=docket_id:{id}`) |
|------------|------------------------------------------------------|
| `docketentry_description` | `description` on the hit (entry-level) |
| `docketentry_date_filed` | `entry_date_filed` |
| `recapdocument_document_number` | `document_number` |
| `recapdocument_attachment_number` | `attachment_number` |
| `recapdocument_description` | `short_description` |
| `recapdocument_is_available` | `is_available` |
| `recapdocument_filepath_local` | `filepath_local` (relative — prefix when downloading) |
| `recapdocument_page_count` | `page_count` |
| `recapdocument_document_type` | `document_type` |

`court_docket_processor.py` only reads the columns listed above, so that minimum is enough — the real UI export includes more docket-level metadata columns that the processor ignores.

### Known limitation: minute entries are dropped

The search-endpoint path enumerates RECAP **documents**, not docket entries. Text-only entries with no attached filing (minute entries, scheduling notices, etc.) won't appear in the CSV. The UI export includes them.

If the user needs full parity with the UI export, warn them about this gap up front. Options:

1. Accept the gap (most use cases only care about filings with PDFs anyway).
2. Fall back to `/docket-entries/?docket={id}` and stitch the two result sets together — but that endpoint 403s on some tokens, so it's not reliable.
3. Have the user click "Export" in the CourtListener UI and hand you the file.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Reaching for `/docket-entries/` first | Use `/search/?type=rd&q=docket_id:{id}` — works with more tokens, fewer round-trips |
| Treating `filepath_local` as a full URL | Often relative — prefix `https://storage.courtlistener.com/` if it doesn't start with `http` |
| Rewriting a user-supplied office prefix to `0:` | Preserve whatever prefix they gave (`2:`, `3:`, etc.); only guess `0:` when no prefix was given |
| Looping over `docket["docket_entries"]` | That field is empty/missing — query the entries/search endpoint |
| Treating `entry["recap_documents"]` as objects | They're URLs; fetch each. Or skip entirely by using the search endpoint. |
| Downloading when `is_available` is false | It'll 403 or 404 — check the flag first |
| Using `order_by=-date_filed` on `/docket-entries/` | Returns 403 — sort client-side. (It works on `/dockets/`; use `order_by=dateFiled desc` on `/search/`.) |
| Using `caseName` on Dockets API or `case_name` on Search API | Field casing differs — Search is camelCase, Dockets is snake_case. Normalize at the boundary. |
| Using `type=rd` to find cases | `rd` returns documents. Use `type=r` for case-discovery queries. |
| Forgetting pagination | Long cases span 20+ pages of results |
| Using `/api/rest/v3/` | Deprecated — move to v4 |
| Skipping `time.sleep` between requests | 429 errors, possible throttling |

## Output conventions (existing project)

When extending the existing court-listener project:

- Per-case folders: `{case_number}_{defendant-or-petitioner-name}/`
- PDF filenames: `order_{date}_doc{n}.pdf` (habeas workflow) or `{doc_num}_{TYPE}.pdf` (docket processor)
- Skip re-downloading if PDF already exists (don't re-fetch over the network)
- Timestamp aggregate outputs (`_YYYYMMDD_HHMMSS`) so re-runs don't clobber prior results
