#!/usr/bin/env python3
"""Search CourtListener for Minnesota habeas cases listed in a CSV.

Reads a CSV with `case_number`, `petitioner_name`, `order_dates` columns and
adds `courtlistener_found`, `docket_url`, `search_notes` in place. The
order downloader script reads the columns this writes.
"""

import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

API_BASE = "https://www.courtlistener.com/api/rest/v4"
RATE_LIMIT_DELAY = 0.25  # 4 req/s; CourtListener allows 5


def normalize_case_number(case_number: str) -> str:
    """`26-CV-0107` -> `0:26-cv-00107`. Hardcoded `0:` office is fine for the
    Minnesota district this script targets; adapt if used elsewhere."""
    parts = case_number.split("-")
    if len(parts) != 3:
        return case_number
    year, kind, num = parts
    return f"0:{year}-{kind.lower()}-{num.zfill(5)}"


def search_case(headers, case_number, court="mnd"):
    """Search dockets for a single case. Returns a docket dict, a multi-match
    marker, or None."""
    r = requests.get(
        f"{API_BASE}/dockets/",
        params={"docket_number": normalize_case_number(case_number), "court": court},
        headers=headers, timeout=30,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        return None
    if len(results) > 1:
        return {"results": results, "ambiguous": True}
    return results[0]


def process_csv(csv_path, api_token, output_dir="minnesota-habeas-orders"):
    os.makedirs(output_dir, exist_ok=True)
    headers = {"Authorization": f"Token {api_token}"}
    df = pd.read_csv(csv_path)

    found, not_found, ambiguous = [], [], []

    for idx, row in df.iterrows():
        case_number = row["case_number"]
        try:
            result = search_case(headers, case_number)
        except requests.RequestException as e:
            print(f"[{idx + 1}/{len(df)}] {case_number}: error {e}")
            not_found.append(row.to_dict())
            df.at[idx, "courtlistener_found"] = False
            df.at[idx, "search_notes"] = f"error: {e}"
            time.sleep(RATE_LIMIT_DELAY)
            continue

        if result is None:
            not_found.append(row.to_dict())
            df.at[idx, "courtlistener_found"] = False
            df.at[idx, "search_notes"] = "Not found"
        elif result.get("ambiguous"):
            ambiguous.append(row.to_dict())
            df.at[idx, "courtlistener_found"] = True
            df.at[idx, "search_notes"] = f"Multiple matches ({len(result['results'])})"
        else:
            df.at[idx, "courtlistener_found"] = True
            df.at[idx, "docket_url"] = f"https://www.courtlistener.com{result.get('absolute_url', '')}"
            found.append({**row.to_dict(),
                          "docket_id": result.get("id"),
                          "case_name": result.get("case_name", "")})
        time.sleep(RATE_LIMIT_DELAY)

    df.to_csv(csv_path, index=False)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_cases": len(df),
        "found": len(found),
        "not_found": len(not_found),
        "ambiguous": len(ambiguous),
        "found_cases": found,
        "not_found_cases": not_found,
        "ambiguous_cases": ambiguous,
    }
    with open(os.path.join(output_dir, "search_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Found {len(found)}/{len(df)} ({len(not_found)} missing, {len(ambiguous)} ambiguous)")
    return summary


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "minnesota_habeas_cases.csv"
    api_token = (sys.argv[2] if len(sys.argv) > 2 else None) or os.environ.get("COURTLISTENER_API_TOKEN")
    if not api_token:
        sys.exit("error: set COURTLISTENER_API_TOKEN or pass token as argv[2]")
    if not os.path.exists(csv_path):
        sys.exit(f"error: {csv_path} not found")
    process_csv(csv_path, api_token)


if __name__ == "__main__":
    main()
