#!/usr/bin/env python3
"""Download court orders for cases already located by the habeas searcher.

Reads the CSV the searcher updated (uses `docket_url`, `order_dates`) and
fetches the PDFs whose docket-entry `date_filed` matches an expected order
date. Skips cases not found on CourtListener.
"""

import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

API_BASE = "https://www.courtlistener.com/api/rest/v4"
STORAGE_BASE = "https://storage.courtlistener.com/"
RATE_LIMIT_DELAY = 0.5  # gentler for PDF fetches


def get_docket_entries(headers, docket_id, max_pages=10):
    """Paginate `/docket-entries/?docket={id}`. May 403 on some tokens; the
    skill recommends `/search/?type=rd&q=docket_id:{id}` as the primary path."""
    entries = []
    url = f"{API_BASE}/docket-entries/"
    params = {"docket": docket_id}
    pages = 0
    while url and pages < max_pages:
        r = requests.get(url, params=params if pages == 0 else None,
                         headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        entries.extend(data.get("results", []))
        url = data.get("next")
        pages += 1
        if url:
            time.sleep(RATE_LIMIT_DELAY)
    return entries


def find_orders(entries, order_dates_str):
    """Match entries whose `date_filed` ISO date appears in the
    semicolon-separated `order_dates` string (e.g. "January 15, 2026")."""
    targets = {}
    for raw in order_dates_str.split(";"):
        raw = raw.strip()
        try:
            iso = datetime.strptime(raw, "%B %d, %Y").strftime("%Y-%m-%d")
            targets[iso] = raw
        except ValueError:
            pass

    matches = []
    for entry in entries:
        iso = entry.get("date_filed", "")
        if iso in targets and "order" in entry.get("description", "").lower():
            for recap_url in entry.get("recap_documents", []):
                matches.append((targets[iso], entry, recap_url))
    return matches


def download_pdf(headers, recap_url, output_path):
    """Fetch RECAP doc metadata, then fetch the PDF if `is_available`."""
    r = requests.get(recap_url, headers=headers, timeout=30)
    r.raise_for_status()
    doc = r.json()
    if not doc.get("is_available"):
        return None
    pdf_url = doc.get("filepath_local")
    if not pdf_url:
        return None
    if not pdf_url.startswith("http"):
        pdf_url = STORAGE_BASE + pdf_url.lstrip("/")
    pdf = requests.get(pdf_url, headers=headers, timeout=60)
    pdf.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(pdf.content)
    return doc


def process_case(headers, row, output_dir):
    docket_url = row["docket_url"]
    docket_id = docket_url.split("/docket/")[1].split("/")[0]

    case_dir = os.path.join(
        output_dir,
        f"{row['case_number']}_{row['petitioner_name'].replace(' ', '-')}",
    )
    os.makedirs(case_dir, exist_ok=True)

    entries = get_docket_entries(headers, docket_id)
    matches = find_orders(entries, row["order_dates"])
    downloaded = []

    for order_date, entry, recap_url in matches:
        date_slug = order_date.replace(", ", "-").replace(" ", "-")
        # Use the recap document id as the filename suffix for uniqueness.
        doc_id = recap_url.rstrip("/").rsplit("/", 1)[-1]
        path = os.path.join(case_dir, f"order_{date_slug}_doc{doc_id}.pdf")
        try:
            doc = download_pdf(headers, recap_url, path)
        except requests.RequestException as e:
            print(f"  download error: {e}")
            continue
        if doc:
            downloaded.append({
                "order_date": order_date,
                "filename": os.path.basename(path),
                "description": entry.get("description", ""),
            })
        time.sleep(RATE_LIMIT_DELAY)

    with open(os.path.join(case_dir, "case_info.json"), "w") as f:
        json.dump({
            "case_number": row["case_number"],
            "petitioner": row["petitioner_name"],
            "docket_url": docket_url,
            "expected_dates": row["order_dates"],
            "downloads": downloaded,
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)

    return downloaded


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    test_mode = "--test" in sys.argv

    csv_path = next((a for a in args if a.endswith(".csv")),
                    "minnesota_habeas_cases.csv")
    api_token = next((a for a in args if not a.endswith(".csv")), None) \
        or os.environ.get("COURTLISTENER_API_TOKEN")

    if not api_token:
        sys.exit("error: set COURTLISTENER_API_TOKEN or pass token as a positional arg")
    if not os.path.exists(csv_path):
        sys.exit(f"error: {csv_path} not found (run courtlistener_habeas_searcher.py first)")

    headers = {"Authorization": f"Token {api_token}"}
    output_dir = "minnesota-habeas-orders"
    os.makedirs(output_dir, exist_ok=True)

    df = pd.read_csv(csv_path)
    found = df[df["courtlistener_found"] == True]  # noqa: E712
    if test_mode:
        found = found.head(1)

    total_orders = 0
    successes = 0
    for idx, row in found.iterrows():
        try:
            downloaded = process_case(headers, row, output_dir)
        except requests.RequestException as e:
            print(f"[{row['case_number']}] error: {e}")
            continue
        total_orders += len(downloaded)
        if downloaded:
            successes += 1

    print(f"Downloaded {total_orders} orders across {successes}/{len(found)} cases")


if __name__ == "__main__":
    main()
