#!/usr/bin/env python3
"""
CourtListener Docket CSV Processor
Automates processing of federal court dockets downloaded from CourtListener

Usage:
    python court_docket_processor.py <docket.csv> <case_number> <defendant_name>

Example:
    python court_docket_processor.py briggs_docket.csv "25-CR-610" "Dana Briggs"

Updates:
    Re-run with updated CSV to process new entries. Existing PDFs won't be re-downloaded.
    All outputs are timestamped so you never lose data from previous runs.
"""

import pandas as pd
import requests
import os
import sys
import json
from datetime import datetime
from time import sleep
from pathlib import Path


class CourtDocketProcessor:
    """Process CourtListener docket CSVs for investigative journalism"""
    
    # Priority document keywords for download
    PRIORITY_KEYWORDS = [
        'complaint',
        'information',
        'indictment',
        'plea',
        'disposition',
        'dismissal',
        'dismiss',
        'judgment',
        'sentence',
        'memorandum opinion',
        'order',
        'motion',
        'pretrial statement'
    ]
    
    # Event types to extract for timeline
    EVENT_KEYWORDS = {
        'arrest': 'Arrest',
        'complaint': 'Complaint Filed',
        'information': 'Information Filed',
        'indictment': 'Indictment',
        'arraign': 'Arraignment',
        'plea': 'Plea Entered',
        'trial': 'Trial',
        'dismiss': 'Case Dismissed',
        'judgment': 'Judgment',
        'sentence': 'Sentencing',
        'motion': 'Motion Filed',
        'hearing': 'Hearing',
        'bond': 'Bond/Release Order'
    }
    
    def __init__(self, csv_path, case_number, defendant_name):
        """
        Initialize processor
        
        Args:
            csv_path: Path to CourtListener docket CSV
            case_number: Case number (e.g., "25-CR-610")
            defendant_name: Defendant's name
        """
        self.csv_path = csv_path
        self.case_number = case_number
        self.defendant_name = defendant_name
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create output directory structure
        self.case_dir = f"court-records/{case_number}_{self._clean_name(defendant_name)}"
        self.docs_dir = os.path.join(self.case_dir, "documents")
        
        # Create directories
        os.makedirs(self.case_dir, exist_ok=True)
        os.makedirs(self.docs_dir, exist_ok=True)
        
        # Load docket
        print(f"\nLoading docket: {csv_path}")
        self.docket = pd.read_csv(csv_path)
        print(f"  Loaded {len(self.docket)} docket entries")
    
    def _clean_name(self, name):
        """Clean name for use in filenames"""
        # Remove special characters, convert to uppercase
        clean = name.upper().replace(' ', '-').replace(',', '')
        # Keep only letters, hyphens, numbers
        clean = ''.join(c for c in clean if c.isalnum() or c == '-')
        return clean
    
    def _extract_doc_type(self, row):
        """Extract meaningful document type from description fields"""
        # Try docketentry_description first (usually has the best info)
        desc = str(row['docketentry_description']).upper()
        
        # Common document type patterns
        type_patterns = {
            'COMPLAINT': 'COMPLAINT',
            'CRIMINAL COMPLAINT': 'CRIMINAL-COMPLAINT',
            'INFORMATION': 'INFORMATION',
            'INDICTMENT': 'INDICTMENT',
            'MOTION TO DISMISS': 'MOTION-TO-DISMISS',
            'MOTION TO': 'MOTION',
            'MOTION FOR': 'MOTION',
            'MEMORANDUM': 'MEMORANDUM',
            'ORDER': 'ORDER',
            'MINUTE ORDER': 'MINUTE-ORDER',
            'MINUTE ENTRY': 'MINUTE-ENTRY',
            'JUDGMENT': 'JUDGMENT',
            'PLEA AGREEMENT': 'PLEA-AGREEMENT',
            'SENTENCING': 'SENTENCING',
            'PRETRIAL': 'PRETRIAL',
            'NOTICE': 'NOTICE',
            'STIPULATION': 'STIPULATION',
            'AFFIDAVIT': 'AFFIDAVIT',
            'DECLARATION': 'DECLARATION',
            'EXHIBIT': 'EXHIBIT',
            'ATTACHMENT': 'ATTACHMENT',
            'RESPONSE': 'RESPONSE',
            'REPLY': 'REPLY',
            'OPPOSITION': 'OPPOSITION',
            'BRIEF': 'BRIEF',
            'TRANSCRIPT': 'TRANSCRIPT',
            'WARRANT': 'WARRANT',
            'SUMMONS': 'SUMMONS'
        }
        
        # Check for patterns in order (more specific first)
        for pattern, doc_type in type_patterns.items():
            if pattern in desc:
                return doc_type
        
        # Fall back to recapdocument_description if available and not generic
        recap_desc = str(row.get('recapdocument_description', ''))
        if recap_desc and recap_desc not in ['nan', 'PACER Document', '']:
            # Take first 30 chars and clean
            return self._clean_name(recap_desc[:30])
        
        # Last resort: use document type field
        doc_type = row.get('recapdocument_document_type', 'Document')
        if doc_type and doc_type != 'PACER Document':
            return self._clean_name(str(doc_type))
        
        # Ultimate fallback
        return 'DOCUMENT'
    
    def is_priority_document(self, row):
        """Check if document is priority based on description"""
        desc = str(row['docketentry_description']).lower()
        doc_type = str(row['recapdocument_description']).lower()
        
        return any(keyword in desc or keyword in doc_type 
                   for keyword in self.PRIORITY_KEYWORDS)
    
    def download_priority_documents(self):
        """Download priority documents from docket"""
        print("\n" + "="*80)
        print("DOWNLOADING PRIORITY DOCUMENTS")
        print("="*80)
        
        # Filter for available priority documents
        available = self.docket[
            (self.docket['recapdocument_is_available'] == True) &
            (self.docket['recapdocument_filepath_local'].notna())
        ].copy()
        
        available['is_priority'] = available.apply(self.is_priority_document, axis=1)
        priority = available[available['is_priority']]
        
        print(f"\nFound {len(priority)} priority documents")
        
        downloaded = []
        skipped_existing = []
        
        for idx, row in priority.iterrows():
            doc_num = row['recapdocument_document_number']
            
            # Handle attachments (e.g., Doc 35.1, 35.2)
            attachment_num = row['recapdocument_attachment_number']
            if pd.notna(attachment_num) and str(attachment_num) != '':
                doc_id = f"{doc_num}.{attachment_num}"
            else:
                doc_id = str(doc_num)
            
            doc_type = row['recapdocument_document_type'] or 'Document'
            doc_desc = row['recapdocument_description'] or row['docketentry_description']
            url = row['recapdocument_filepath_local']
            pages = row['recapdocument_page_count']
            
            # Extract meaningful document type from description
            meaningful_type = self._extract_doc_type(row)
            
            # Create filename using meaningful type
            filename = f"{doc_id}_{meaningful_type}.pdf"
            filepath = os.path.join(self.docs_dir, filename)
            
            # Check if already exists - SKIP RE-DOWNLOAD
            if os.path.exists(filepath):
                print(f"  ○ Already have: {filename}")
                skipped_existing.append({
                    'doc_number': doc_id,
                    'doc_type': doc_type,
                    'description': doc_desc[:100],
                    'filename': filename,
                    'filepath': filepath,
                    'pages': pages,
                    'url': url,
                    'filing_date': row['docketentry_date_filed'],
                    'status': 'Previously downloaded'
                })
                continue
            
            # Download if we don't have it yet
            try:
                print(f"\n✓ Downloading Doc #{doc_id}: {doc_type}")
                print(f"  URL: {url}")
                
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                
                downloaded.append({
                    'doc_number': doc_id,
                    'doc_type': doc_type,
                    'description': doc_desc[:100],
                    'filename': filename,
                    'filepath': filepath,
                    'pages': pages,
                    'url': url,
                    'filing_date': row['docketentry_date_filed'],
                    'status': 'Downloaded this run'
                })
                
                print(f"  ✓ Saved: {filename} ({pages} pages)")
                
                # Be polite to server
                sleep(1)
                
            except Exception as e:
                print(f"  ✗ Failed: {filename}")
                print(f"     Error: {e}")
        
        # Combine both lists for logging
        all_docs = downloaded + skipped_existing
        
        # Save download log with timestamp
        if all_docs:
            download_log = pd.DataFrame(all_docs)
            log_path = os.path.join(self.case_dir, f'downloaded_documents_{self.timestamp}.csv')
            download_log.to_csv(log_path, index=False)
            
            print(f"\n" + "="*80)
            print(f"✓ NEW downloads this run: {len(downloaded)}")
            print(f"○ Already had from previous runs: {len(skipped_existing)}")
            print(f"  Total priority documents: {len(all_docs)}")
            print(f"  Log saved: {log_path}")
        
        return all_docs, len(downloaded), len(skipped_existing)
    
    def extract_key_events(self):
        """Extract timeline of key events from docket"""
        print("\n" + "="*80)
        print("EXTRACTING KEY EVENTS")
        print("="*80)
        
        events = []
        
        for idx, row in self.docket.iterrows():
            desc = str(row['docketentry_description']).lower()
            date = row['docketentry_date_filed']
            doc_num = row['recapdocument_document_number']
            
            # Check for each event type
            for keyword, event_type in self.EVENT_KEYWORDS.items():
                if keyword in desc:
                    # Extract more specific details based on event type
                    detail = self._extract_event_detail(desc, event_type)
                    
                    events.append({
                        'date': date,
                        'event_type': event_type,
                        'detail': detail,
                        'doc_number': doc_num if pd.notna(doc_num) else '',
                        'full_description': row['docketentry_description']
                    })
                    break  # Only count first matching event type per entry
        
        # Sort by date
        events_df = pd.DataFrame(events)
        if not events_df.empty:
            events_df = events_df.sort_values('date')
        
        # Save timeline with timestamp
        timeline_path = os.path.join(self.case_dir, f'timeline_{self.timestamp}.csv')
        events_df.to_csv(timeline_path, index=False)
        
        print(f"\nExtracted {len(events)} key events:")
        for idx, event in events_df.iterrows():
            print(f"  {event['date']}: {event['event_type']}")
            if event['detail']:
                print(f"    → {event['detail']}")
        
        print(f"\n✓ Timeline saved: {timeline_path}")
        
        return events_df
    
    def _extract_event_detail(self, description, event_type):
        """Extract specific details from event description"""
        desc_lower = description.lower()
        
        # Extract specific details based on event type
        if event_type == 'Case Dismissed':
            if 'with prejudice' in desc_lower:
                return 'Dismissed WITH PREJUDICE (cannot refile)'
            elif 'without prejudice' in desc_lower:
                return 'Dismissed without prejudice (can refile)'
            else:
                return 'Dismissed'
        
        elif event_type == 'Plea Entered':
            if 'guilty' in desc_lower:
                return 'Guilty plea'
            elif 'not guilty' in desc_lower:
                return 'Not guilty plea'
            else:
                return 'Plea entered'
        
        elif event_type == 'Bond/Release Order':
            # Try to extract bond amount
            import re
            amount_match = re.search(r'\$[\d,]+', description)
            if amount_match:
                return f'Bond set: {amount_match.group()}'
            else:
                return 'Release conditions set'
        
        return ''  # No specific detail
    
    def determine_case_status(self):
        """Determine current case status from docket"""
        print("\n" + "="*80)
        print("DETERMINING CASE STATUS")
        print("="*80)
        
        # Get latest entries
        latest_entries = self.docket.tail(5)
        
        status = 'Pending'
        status_date = None
        status_detail = ''
        
        # Check from most recent backwards
        for idx, row in latest_entries.iloc[::-1].iterrows():
            desc = str(row['docketentry_description']).lower()
            date = row['docketentry_date_filed']
            
            if 'dismiss' in desc:
                if 'with prejudice' in desc:
                    status = 'Dismissed with Prejudice'
                    status_detail = 'Case closed - cannot be refiled'
                else:
                    status = 'Dismissed'
                    status_detail = 'Case closed'
                status_date = date
                break
            
            elif 'judgment' in desc or 'sentence' in desc:
                status = 'Closed - Sentenced'
                status_date = date
                break
            
            elif 'plea' in desc and 'guilty' in desc:
                status = 'Pending - Guilty Plea Entered'
                status_detail = 'Awaiting sentencing'
                break
            
            elif 'trial' in desc:
                status = 'Pending - Trial Scheduled'
                break
        
        print(f"\nCase Status: {status}")
        if status_date:
            print(f"  Status Date: {status_date}")
        if status_detail:
            print(f"  Detail: {status_detail}")
        
        return {
            'status': status,
            'status_date': status_date,
            'status_detail': status_detail
        }
    
    def generate_database_entries(self, status_info):
        """Generate database entries for court_cases and court_documents"""
        print("\n" + "="*80)
        print("GENERATING DATABASE ENTRIES")
        print("="*80)
        
        # Extract case metadata from first entries
        first_entry = self.docket.iloc[0]
        filed_date = first_entry['docketentry_date_filed']
        
        # Find complaint or information to get charge details
        complaint = self.docket[
            self.docket['docketentry_description'].str.contains(
                'COMPLAINT|INFORMATION', 
                case=False, 
                na=False
            )
        ]
        
        charge = '18 USC 111'  # Default assumption based on your investigation
        if not complaint.empty:
            complaint_desc = complaint.iloc[0]['docketentry_description']
            if '111' in complaint_desc:
                if '(a)(1)' in complaint_desc or 'misdemeanor' in complaint_desc.lower():
                    charge = '18 USC 111(a)(1) - Simple Assault (misdemeanor)'
                elif '(b)' in complaint_desc or 'felony' in complaint_desc.lower():
                    charge = '18 USC 111(b) - Assault with Weapon/Injury (felony)'
        
        # Generate court_cases entry
        court_case = {
            'case_number': self.case_number,
            'court': 'N.D. Illinois',
            'filed_date': filed_date,
            'defendant': self.defendant_name,
            'case_type': 'Criminal',
            'nature_of_suit': charge,
            'plaintiff': 'United States of America',
            'judge': 'Gabriela A. Fuentes',  # May need to extract
            'status': status_info['status'],
            'disposition': status_info['status'],
            'disposition_date': status_info['status_date'] if status_info['status_date'] else '',
            'notes': status_info['status_detail']
        }
        
        # Generate court_documents entries
        court_docs = []
        
        available = self.docket[
            (self.docket['recapdocument_is_available'] == True) &
            (self.docket['recapdocument_filepath_local'].notna())
        ]
        
        for idx, row in available.iterrows():
            doc_num = row['recapdocument_document_number']
            attachment_num = row['recapdocument_attachment_number']
            
            if pd.notna(attachment_num) and str(attachment_num) != '':
                doc_id = f"{doc_num}.{attachment_num}"
            else:
                doc_id = str(doc_num)
            
            doc_type = row['recapdocument_document_type'] or 'Document'
            meaningful_type = self._extract_doc_type(row)
            
            doc_entry = {
                'case_number': self.case_number,
                'document_number': doc_id,
                'document_type': doc_type,
                'filing_date': row['docketentry_date_filed'],
                'description': row['docketentry_description'][:200],  # Truncate
                'file_path': f"{self.docs_dir}/{doc_id}_{meaningful_type}.pdf",
                'courtlistener_url': row['recapdocument_filepath_local'],
                'page_count': row['recapdocument_page_count']
            }
            court_docs.append(doc_entry)
        
        # Save to CSV with timestamps
        court_case_df = pd.DataFrame([court_case])
        court_docs_df = pd.DataFrame(court_docs)
        
        case_path = os.path.join(self.case_dir, f'court_case_entry_{self.timestamp}.csv')
        docs_path = os.path.join(self.case_dir, f'court_documents_entries_{self.timestamp}.csv')
        
        court_case_df.to_csv(case_path, index=False)
        court_docs_df.to_csv(docs_path, index=False)
        
        print(f"\n✓ Generated court_cases entry:")
        print(f"  Case: {self.case_number} - {self.defendant_name}")
        print(f"  Status: {status_info['status']}")
        print(f"  Saved: {case_path}")
        
        print(f"\n✓ Generated {len(court_docs)} court_documents entries:")
        print(f"  Saved: {docs_path}")
        
        return court_case_df, court_docs_df
    
    def generate_summary_report(self, all_docs, new_count, existing_count, events_df, status_info):
        """Generate human-readable summary report"""
        print("\n" + "="*80)
        print("GENERATING SUMMARY REPORT")
        print("="*80)
        
        report_lines = []
        report_lines.append("# CASE PROCESSING REPORT")
        report_lines.append(f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"**Case:** {self.case_number}")
        report_lines.append(f"**Defendant:** {self.defendant_name}")
        report_lines.append(f"**Status:** {status_info['status']}")
        if status_info['status_date']:
            report_lines.append(f"**Status Date:** {status_info['status_date']}")
        if status_info['status_detail']:
            report_lines.append(f"**Detail:** {status_info['status_detail']}")
        
        report_lines.append("\n## TIMELINE OF KEY EVENTS\n")
        if not events_df.empty:
            for idx, event in events_df.iterrows():
                report_lines.append(f"- **{event['date']}:** {event['event_type']}")
                if event['detail']:
                    report_lines.append(f"  - {event['detail']}")
        else:
            report_lines.append("No events extracted")
        
        report_lines.append("\n## DOCUMENT DOWNLOAD SUMMARY\n")
        report_lines.append(f"- **NEW downloads this run:** {new_count}")
        report_lines.append(f"- **Already had from previous runs:** {existing_count}")
        report_lines.append(f"- **Total priority documents:** {len(all_docs)}")
        
        # Show new documents if any
        if new_count > 0:
            report_lines.append("\n### New Documents Downloaded\n")
            new_docs = [d for d in all_docs if d['status'] == 'Downloaded this run']
            for doc in new_docs:
                report_lines.append(f"- **Doc #{doc['doc_number']}:** {doc['doc_type']} ({doc['pages']} pages)")
                report_lines.append(f"  - File: `{doc['filename']}`")
        
        # Show existing if any
        if existing_count > 0:
            report_lines.append("\n### Previously Downloaded Documents\n")
            report_lines.append(f"- {existing_count} documents from previous runs (not re-downloaded)")
        
        report_lines.append("\n## FILES CREATED THIS RUN\n")
        report_lines.append(f"- Case folder: `{self.case_dir}/`")
        report_lines.append(f"- Documents folder: `{self.docs_dir}/`")
        report_lines.append(f"- Database entry: `court_case_entry_{self.timestamp}.csv`")
        report_lines.append(f"- Database entries: `court_documents_entries_{self.timestamp}.csv`")
        report_lines.append(f"- Timeline: `timeline_{self.timestamp}.csv`")
        report_lines.append(f"- Download log: `downloaded_documents_{self.timestamp}.csv`")
        report_lines.append(f"- Summary: `CASE_SUMMARY_{self.timestamp}.md`")
        
        report_lines.append("\n## NEXT STEPS\n")
        report_lines.append("1. Review downloaded documents for key details")
        report_lines.append("2. Import database entries into investigation database")
        report_lines.append("3. Cross-reference with arrest spreadsheet")
        report_lines.append("4. Note any important findings in case notes")
        
        if existing_count > 0:
            report_lines.append("\n## UPDATE NOTES\n")
            report_lines.append(f"- This is an update run (found {existing_count} existing documents)")
            report_lines.append(f"- Compare this report with previous runs to see changes")
            report_lines.append(f"- All output files are timestamped: `*_{self.timestamp}.csv`")
        
        report = '\n'.join(report_lines)
        
        # Save report with timestamp
        report_path = os.path.join(self.case_dir, f'CASE_SUMMARY_{self.timestamp}.md')
        with open(report_path, 'w') as f:
            f.write(report)
        
        print(report)
        print(f"\n✓ Summary report saved: {report_path}")
        
        return report
    
    def process(self):
        """Run complete processing workflow"""
        print("\n" + "="*80)
        print(f"PROCESSING CASE: {self.case_number} - {self.defendant_name}")
        print(f"Run timestamp: {self.timestamp}")
        print("="*80)
        
        # 1. Download priority documents
        all_docs, new_count, existing_count = self.download_priority_documents()
        
        # 2. Extract key events
        events_df = self.extract_key_events()
        
        # 3. Determine case status
        status_info = self.determine_case_status()
        
        # 4. Generate database entries
        court_case_df, court_docs_df = self.generate_database_entries(status_info)
        
        # 5. Generate summary report
        report = self.generate_summary_report(all_docs, new_count, existing_count, events_df, status_info)
        
        print("\n" + "="*80)
        print("✓ PROCESSING COMPLETE")
        print("="*80)
        print(f"\nAll files saved to: {self.case_dir}/")
        
        if new_count > 0:
            print(f"✓ Downloaded {new_count} new documents")
        if existing_count > 0:
            print(f"○ Skipped {existing_count} existing documents (already had them)")
        
        return {
            'case_dir': self.case_dir,
            'new_downloads': new_count,
            'existing_docs': existing_count,
            'total_docs': len(all_docs),
            'events': len(events_df),
            'status': status_info['status'],
            'timestamp': self.timestamp
        }


def main():
    """Main entry point"""
    if len(sys.argv) != 4:
        print("Usage: python court_docket_processor.py <docket.csv> <case_number> <defendant_name>")
        print("\nExample:")
        print('  python court_docket_processor.py briggs_docket.csv "25-CR-610" "Dana Briggs"')
        print("\nUpdate workflow:")
        print('  1. Download updated CSV from CourtListener')
        print('  2. Re-run with updated CSV - existing PDFs will be skipped')
        print('  3. New outputs are timestamped so you never lose previous data')
        sys.exit(1)
    
    csv_path = sys.argv[1]
    case_number = sys.argv[2]
    defendant_name = sys.argv[3]
    
    # Validate inputs
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)
    
    # Process docket
    processor = CourtDocketProcessor(csv_path, case_number, defendant_name)
    result = processor.process()
    
    print(f"\n✓ Success!")
    print(f"  New downloads: {result['new_downloads']}")
    print(f"  Skipped (already had): {result['existing_docs']}")
    print(f"  Total documents: {result['total_docs']}")
    print(f"  Events tracked: {result['events']}")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['case_dir']}/")
    print(f"  Timestamp: {result['timestamp']}")


if __name__ == "__main__":
    main()
