#!/usr/bin/env python3
"""Process a CourtListener docket CSV (UI export with `recapdocument_*` columns).

Downloads priority documents, builds a timeline, infers case status, and
emits DB-ready CSVs. Re-runs are incremental: existing PDFs are not
re-downloaded; aggregate outputs are timestamped so prior runs aren't lost.

Usage:
    python court_docket_processor.py <docket.csv> <case_number> <defendant_name>
"""

import os
import re
import sys
from datetime import datetime
from time import sleep

import pandas as pd
import requests

PRIORITY_KEYWORDS = [
    "complaint", "information", "indictment", "plea", "disposition",
    "dismissal", "dismiss", "judgment", "sentence", "memorandum opinion",
    "order", "motion", "pretrial statement",
]

EVENT_KEYWORDS = {
    "arrest": "Arrest", "complaint": "Complaint Filed",
    "information": "Information Filed", "indictment": "Indictment",
    "arraign": "Arraignment", "plea": "Plea Entered", "trial": "Trial",
    "dismiss": "Case Dismissed", "judgment": "Judgment",
    "sentence": "Sentencing", "motion": "Motion Filed",
    "hearing": "Hearing", "bond": "Bond/Release Order",
}

DOC_TYPE_PATTERNS = [
    ("CRIMINAL COMPLAINT", "CRIMINAL-COMPLAINT"),
    ("MOTION TO DISMISS", "MOTION-TO-DISMISS"),
    ("MOTION TO", "MOTION"), ("MOTION FOR", "MOTION"),
    ("MINUTE ORDER", "MINUTE-ORDER"), ("MINUTE ENTRY", "MINUTE-ENTRY"),
    ("PLEA AGREEMENT", "PLEA-AGREEMENT"),
    ("COMPLAINT", "COMPLAINT"), ("INFORMATION", "INFORMATION"),
    ("INDICTMENT", "INDICTMENT"), ("MEMORANDUM", "MEMORANDUM"),
    ("ORDER", "ORDER"), ("JUDGMENT", "JUDGMENT"),
    ("SENTENCING", "SENTENCING"), ("PRETRIAL", "PRETRIAL"),
    ("NOTICE", "NOTICE"), ("STIPULATION", "STIPULATION"),
    ("AFFIDAVIT", "AFFIDAVIT"), ("DECLARATION", "DECLARATION"),
    ("EXHIBIT", "EXHIBIT"), ("ATTACHMENT", "ATTACHMENT"),
    ("RESPONSE", "RESPONSE"), ("REPLY", "REPLY"),
    ("OPPOSITION", "OPPOSITION"), ("BRIEF", "BRIEF"),
    ("TRANSCRIPT", "TRANSCRIPT"), ("WARRANT", "WARRANT"),
    ("SUMMONS", "SUMMONS"),
]


def clean_name(name: str) -> str:
    return "".join(c for c in name.upper().replace(" ", "-").replace(",", "")
                   if c.isalnum() or c == "-")


def extract_doc_type(row) -> str:
    """Pick a meaningful filename suffix from the row's description fields."""
    desc = str(row["docketentry_description"]).upper()
    for pattern, label in DOC_TYPE_PATTERNS:
        if pattern in desc:
            return label
    recap_desc = str(row.get("recapdocument_description", ""))
    if recap_desc and recap_desc not in ("nan", "PACER Document", ""):
        return clean_name(recap_desc[:30])
    doc_type = row.get("recapdocument_document_type") or "Document"
    return clean_name(str(doc_type)) if doc_type != "PACER Document" else "DOCUMENT"


def doc_id_for(row) -> str:
    num = row["recapdocument_document_number"]
    att = row.get("recapdocument_attachment_number")
    return f"{num}.{att}" if pd.notna(att) and str(att) != "" else str(num)


def is_priority(row) -> bool:
    desc = str(row["docketentry_description"]).lower()
    rdesc = str(row.get("recapdocument_description", "")).lower()
    return any(k in desc or k in rdesc for k in PRIORITY_KEYWORDS)


def download_priority_documents(docket, docs_dir):
    """Download priority docs whose RECAP files are available. Skip if the
    target PDF already exists on disk."""
    available = docket[
        (docket["recapdocument_is_available"] == True) &  # noqa: E712
        (docket["recapdocument_filepath_local"].notna())
    ]
    priority = available[available.apply(is_priority, axis=1)]

    new, existing = [], []
    for _, row in priority.iterrows():
        did = doc_id_for(row)
        filename = f"{did}_{extract_doc_type(row)}.pdf"
        path = os.path.join(docs_dir, filename)
        record = {
            "doc_number": did,
            "filename": filename,
            "filing_date": row["docketentry_date_filed"],
            "pages": row["recapdocument_page_count"],
            "description": str(row.get("recapdocument_description")
                               or row["docketentry_description"])[:100],
        }
        if os.path.exists(path):
            existing.append(record)
            continue
        try:
            r = requests.get(row["recapdocument_filepath_local"], timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            new.append(record)
            sleep(1)  # be polite to the storage server
        except requests.RequestException as e:
            print(f"  failed: {filename}: {e}")
    return new, existing


def extract_events(docket):
    """Pull a date-sorted timeline; first matching keyword wins per row."""
    events = []
    for _, row in docket.iterrows():
        desc = str(row["docketentry_description"])
        desc_lower = desc.lower()
        for kw, label in EVENT_KEYWORDS.items():
            if kw in desc_lower:
                events.append({
                    "date": row["docketentry_date_filed"],
                    "event_type": label,
                    "detail": event_detail(desc_lower, label),
                    "doc_number": row["recapdocument_document_number"]
                        if pd.notna(row["recapdocument_document_number"]) else "",
                    "full_description": desc,
                })
                break
    df = pd.DataFrame(events)
    return df.sort_values("date") if not df.empty else df


def event_detail(desc_lower: str, event_type: str) -> str:
    if event_type == "Case Dismissed":
        if "with prejudice" in desc_lower:
            return "Dismissed WITH PREJUDICE (cannot refile)"
        if "without prejudice" in desc_lower:
            return "Dismissed without prejudice (can refile)"
        return "Dismissed"
    if event_type == "Plea Entered":
        if "guilty" in desc_lower and "not guilty" not in desc_lower:
            return "Guilty plea"
        if "not guilty" in desc_lower:
            return "Not guilty plea"
        return "Plea entered"
    if event_type == "Bond/Release Order":
        m = re.search(r"\$[\d,]+", desc_lower)
        return f"Bond set: {m.group()}" if m else "Release conditions set"
    return ""


def determine_status(docket):
    """Walk the last 5 entries in reverse; first match wins. Order matters:
    dismissal > sentencing > guilty plea > scheduled trial."""
    for _, row in docket.tail(5).iloc[::-1].iterrows():
        desc = str(row["docketentry_description"]).lower()
        date = row["docketentry_date_filed"]
        if "dismiss" in desc:
            if "with prejudice" in desc:
                return {"status": "Dismissed with Prejudice",
                        "status_date": date,
                        "status_detail": "Case closed - cannot be refiled"}
            return {"status": "Dismissed", "status_date": date,
                    "status_detail": "Case closed"}
        if "judgment" in desc or "sentence" in desc:
            return {"status": "Closed - Sentenced", "status_date": date,
                    "status_detail": ""}
        if "plea" in desc and "guilty" in desc:
            return {"status": "Pending - Guilty Plea Entered",
                    "status_date": None,
                    "status_detail": "Awaiting sentencing"}
        if "trial" in desc:
            return {"status": "Pending - Trial Scheduled",
                    "status_date": None, "status_detail": ""}
    return {"status": "Pending", "status_date": None, "status_detail": ""}


def build_db_entries(docket, case_number, defendant_name, status_info, docs_dir):
    """Emit one row for `court_cases` and one row per available doc for
    `court_documents`. Charge default is investigation-specific (18 USC 111)
    — change here if you adapt the script for a different case type."""
    filed_date = docket.iloc[0]["docketentry_date_filed"]

    charge = "18 USC 111"
    complaint = docket[docket["docketentry_description"].str.contains(
        "COMPLAINT|INFORMATION", case=False, na=False)]
    if not complaint.empty:
        text = complaint.iloc[0]["docketentry_description"]
        if "111" in text:
            if "(a)(1)" in text or "misdemeanor" in text.lower():
                charge = "18 USC 111(a)(1) - Simple Assault (misdemeanor)"
            elif "(b)" in text or "felony" in text.lower():
                charge = "18 USC 111(b) - Assault with Weapon/Injury (felony)"

    case_row = {
        "case_number": case_number, "court": "N.D. Illinois",
        "filed_date": filed_date, "defendant": defendant_name,
        "case_type": "Criminal", "nature_of_suit": charge,
        "plaintiff": "United States of America",
        "judge": "Gabriela A. Fuentes",
        "status": status_info["status"],
        "disposition": status_info["status"],
        "disposition_date": status_info["status_date"] or "",
        "notes": status_info["status_detail"],
    }

    available = docket[
        (docket["recapdocument_is_available"] == True) &  # noqa: E712
        (docket["recapdocument_filepath_local"].notna())
    ]
    doc_rows = []
    for _, row in available.iterrows():
        did = doc_id_for(row)
        doc_rows.append({
            "case_number": case_number, "document_number": did,
            "document_type": row.get("recapdocument_document_type") or "Document",
            "filing_date": row["docketentry_date_filed"],
            "description": str(row["docketentry_description"])[:200],
            "file_path": f"{docs_dir}/{did}_{extract_doc_type(row)}.pdf",
            "courtlistener_url": row["recapdocument_filepath_local"],
            "page_count": row["recapdocument_page_count"],
        })
    return pd.DataFrame([case_row]), pd.DataFrame(doc_rows)


def process(csv_path: str, case_number: str, defendant_name: str) -> None:
    docket = pd.read_csv(csv_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_dir = os.path.join("court-records",
                            f"{case_number}_{clean_name(defendant_name)}")
    docs_dir = os.path.join(case_dir, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    new_docs, existing_docs = download_priority_documents(docket, docs_dir)
    events = extract_events(docket)
    status_info = determine_status(docket)
    case_df, docs_df = build_db_entries(
        docket, case_number, defendant_name, status_info, docs_dir,
    )

    events.to_csv(os.path.join(case_dir, f"timeline_{timestamp}.csv"), index=False)
    case_df.to_csv(os.path.join(case_dir, f"court_case_entry_{timestamp}.csv"), index=False)
    docs_df.to_csv(os.path.join(case_dir, f"court_documents_entries_{timestamp}.csv"),
                   index=False)
    pd.DataFrame(new_docs + existing_docs).to_csv(
        os.path.join(case_dir, f"downloaded_documents_{timestamp}.csv"), index=False,
    )

    print(f"{case_number} {defendant_name} -> {case_dir}")
    print(f"  status:      {status_info['status']}")
    print(f"  events:      {len(events)}")
    print(f"  docs:        {len(new_docs)} new, {len(existing_docs)} existing")


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit("usage: court_docket_processor.py <docket.csv> <case_number> <defendant_name>")
    csv_path, case_number, defendant_name = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.exists(csv_path):
        sys.exit(f"error: {csv_path} not found")
    process(csv_path, case_number, defendant_name)


if __name__ == "__main__":
    main()
