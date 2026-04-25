#!/usr/bin/env python3
"""Search CourtListener for recently filed Section 1983 cases and filter
for law-enforcement / corrections matters by keyword."""

import argparse
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests

SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"

KEYWORDS = [
    "police", "officer", "sheriff", "deputy", "detective", "trooper",
    "arrest", "excessive force", "unlawful detention", "false arrest",
    "use of force", "taser", "shooting", "beaten", "assault",
    "prison", "jail", "correctional", "detention", "custody",
    "inmate", "prisoner", "incarcerated", "pretrial detention",
    "conditions of confinement", "deliberate indifference",
    "medical care", "solitary confinement", "segregation",
    "warden", "corrections officer", "jailer", "guards",
    "department of corrections", "bureau of prisons",
    "qualified immunity", "fourth amendment", "eighth amendment",
    "unreasonable seizure", "cruel and unusual punishment",
]


def search(token: Optional[str], max_results: int) -> List[Dict]:
    """Page through `/search/?type=r` for recent civil-rights cases."""
    headers = {"Authorization": f"Token {token}"} if token else {}
    params = {"type": "r", "q": "civil rights 1983", "order_by": "dateFiled desc"}
    results: List[Dict] = []
    cursor = None

    while len(results) < max_results:
        if cursor:
            params["cursor"] = cursor
        r = requests.get(SEARCH_URL, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        page = data.get("results", [])
        if not page:
            break
        results.extend(page)
        m = re.search(r"cursor=([^&]+)", data.get("next") or "")
        if not m:
            break
        cursor = m.group(1)

    return results[:max_results]


def is_relevant(case: Dict) -> bool:
    """Keyword-match against case name, docket number, cause, nature, snippet."""
    text = " ".join(
        str(case.get(k, "")) for k in
        ("caseName", "docketNumber", "cause", "suitNature", "snippet")
    ).lower()
    return any(kw in text for kw in KEYWORDS)


def format_case(case: Dict) -> str:
    url = case.get("absolute_url", "")
    return (
        f"{case.get('caseName', 'N/A')}\n"
        f"  Docket:  {case.get('docketNumber', 'N/A')}  ({case.get('court', 'N/A')})\n"
        f"  Filed:   {case.get('dateFiled', 'N/A')}\n"
        f"  Cause:   {case.get('cause', 'N/A')}\n"
        f"  URL:     https://www.courtlistener.com{url}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-results", type=int, default=100)
    p.add_argument("--output", help="Optional JSON output path")
    p.add_argument("--token", help="API token (or set COURTLISTENER_API_TOKEN)")
    args = p.parse_args()

    token = args.token or os.environ.get("COURTLISTENER_API_TOKEN")
    cases = search(token, args.max_results)
    relevant = [c for c in cases if is_relevant(c)]

    print(f"{len(relevant)} relevant of {len(cases)} fetched\n")
    for i, case in enumerate(relevant, 1):
        print(f"[{i}] {format_case(case)}\n")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(
                {"generated_at": datetime.now().isoformat(),
                 "total": len(relevant), "cases": relevant},
                f, indent=2,
            )


if __name__ == "__main__":
    main()
