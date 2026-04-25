#!/usr/bin/env python3
"""
CourtListener Order Downloader
Downloads court orders for Minnesota habeas cases after search completion

Requires:
- minnesota_habeas_cases.csv with populated docket_url column
- search_summary.json from courtlistener_habeas_searcher.py
"""

import pandas as pd
import requests
import os
import json
import time
from datetime import datetime
from pathlib import Path


class OrderDownloader:
    """Download court orders from CourtListener"""
    
    API_BASE = "https://www.courtlistener.com/api/rest/v4"
    RATE_LIMIT_DELAY = 0.5  # Be more conservative for downloads
    
    def __init__(self, csv_path, api_token=None, output_dir='minnesota-habeas-orders'):
        """Initialize downloader"""
        self.csv_path = csv_path
        self.output_dir = output_dir
        self.api_token = api_token
        os.makedirs(output_dir, exist_ok=True)
        
        # Set up authentication headers
        self.headers = {}
        if api_token:
            self.headers['Authorization'] = f'Token {api_token}'
        
        # Load cases
        print(f"\nLoading cases from: {csv_path}")
        self.df = pd.read_csv(csv_path)
        
        # Filter to only cases found on CourtListener
        self.found_df = self.df[self.df['courtlistener_found'] == True].copy()
        print(f"  Total cases: {len(self.df)}")
        print(f"  Found on CourtListener: {len(self.found_df)}")
        
        # Load search summary if available
        summary_path = os.path.join(output_dir, 'search_summary.json')
        self.search_summary = None
        if os.path.exists(summary_path):
            with open(summary_path, 'r') as f:
                self.search_summary = json.load(f)
            print(f"  Loaded search summary: {summary_path}")
        
        # Track downloads
        self.successful_downloads = []
        self.failed_downloads = []
        self.total_orders_downloaded = 0
    
    def get_docket_entries(self, docket_url):
        """
        Get docket entries from a docket URL
        
        Per CourtListener API docs: Docket entries are NOT embedded in the docket response.
        Must query the docket-entries endpoint and filter by docket ID.
        
        Args:
            docket_url: Full URL like https://www.courtlistener.com/docket/123456/
            
        Returns:
            List of docket entries with documents
        """
        # Extract docket ID from URL
        # Format: https://www.courtlistener.com/docket/12345678/case-name/
        try:
            docket_id = docket_url.split('/docket/')[1].split('/')[0]
        except (IndexError, AttributeError) as e:
            print(f"    ✗ Error parsing docket URL: {docket_url}")
            print(f"       {e}")
            return []
        
        # Use the docket-entries endpoint and filter by docket ID
        # This is the correct way per the API documentation
        api_url = f"{self.API_BASE}/docket-entries/"
        params = {
            'docket': docket_id,
            'format': 'json'
        }
        
        try:
            print(f"    Fetching docket entries for docket ID: {docket_id}")
            response = requests.get(api_url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # API returns paginated results
            entries = data.get('results', [])
            print(f"    Found {len(entries)} docket entries (page 1)")
            
            # Check if there are more pages
            next_url = data.get('next')
            page = 2
            while next_url and page <= 10:  # Limit to 10 pages for safety
                print(f"    Fetching page {page}...")
                response = requests.get(next_url, headers=self.headers, timeout=30)
                response.raise_for_status()
                data = response.json()
                page_entries = data.get('results', [])
                entries.extend(page_entries)
                print(f"    Found {len(page_entries)} more entries (total: {len(entries)})")
                next_url = data.get('next')
                page += 1
                time.sleep(self.RATE_LIMIT_DELAY)
            
            print(f"    Total docket entries retrieved: {len(entries)}")
            return entries
            
        except requests.exceptions.HTTPError as e:
            print(f"    ✗ HTTP Error {e.response.status_code}: {e}")
            if e.response.status_code == 404:
                print(f"    Docket {docket_id} entries not found")
            elif e.response.status_code == 403:
                print(f"    Access forbidden - docket may be sealed")
            return []
        except Exception as e:
            print(f"    ✗ Error getting docket entries: {e}")
            return []
    
    def find_orders_by_date(self, docket_entries, order_dates_str):
        """
        Find orders matching specific dates
        
        Args:
            docket_entries: List of docket entries
            order_dates_str: Semicolon-separated dates
            
        Returns:
            List of (date, entry, recap_document_ids) tuples
        """
        # Parse target dates
        target_dates = []
        for date_str in order_dates_str.split(';'):
            date_str = date_str.strip()
            try:
                dt = datetime.strptime(date_str, "%B %d, %Y")
                target_dates.append({
                    'original': date_str,
                    'iso': dt.strftime("%Y-%m-%d"),
                    'dt': dt
                })
            except Exception as e:
                print(f"    Warning: Could not parse date '{date_str}': {e}")
        
        matches = []
        for entry in docket_entries:
            entry_date = entry.get('date_filed', '')
            
            # Check if this entry matches any target date
            for target in target_dates:
                if entry_date == target['iso']:
                    # Check if it's an order
                    description = entry.get('description', '').lower()
                    if 'order' in description:
                        # Docket entries have a 'recap_documents' field that contains URLs, not full objects
                        # We'll need to fetch these separately
                        recap_doc_urls = entry.get('recap_documents', [])
                        if recap_doc_urls:
                            matches.append((target['original'], entry, recap_doc_urls))
                            print(f"    ✓ Found order for {target['original']}: {description[:60]}")
                            print(f"       {len(recap_doc_urls)} RECAP document(s) associated")
        
        return matches
    
    def fetch_recap_document(self, recap_url):
        """
        Fetch a RECAP document by its URL
        
        Args:
            recap_url: URL to RECAP document (e.g., https://www.courtlistener.com/api/rest/v4/recap-documents/123/)
            
        Returns:
            RECAP document object or None
        """
        try:
            response = requests.get(recap_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"    ⚠ Error fetching RECAP document: {e}")
            return None
    
    def download_pdf(self, pdf_url, output_path):
        """Download a PDF file"""
        try:
            response = requests.get(pdf_url, headers=self.headers, timeout=60)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            file_size = len(response.content)
            print(f"    ✓ Downloaded: {os.path.basename(output_path)} ({file_size:,} bytes)")
            return True
        except Exception as e:
            print(f"    ✗ Download failed: {e}")
            return False
    
    def process_case(self, row):
        """Download orders for a single case"""
        case_number = row['case_number']
        petitioner = row['petitioner_name']
        docket_url = row['docket_url']
        order_dates = row['order_dates']
        
        print(f"\n  Case: {case_number} - {petitioner}")
        print(f"    Orders expected: {row['order_count']}")
        
        # Create case directory
        case_dir = os.path.join(self.output_dir, f"{case_number}_{petitioner.replace(' ', '-')}")
        os.makedirs(case_dir, exist_ok=True)
        
        # Get docket entries
        print(f"    Fetching docket entries...")
        docket_entries = self.get_docket_entries(docket_url)
        
        if not docket_entries:
            self.failed_downloads.append({
                'case_number': case_number,
                'reason': 'Could not fetch docket entries'
            })
            return
        
        print(f"    Found {len(docket_entries)} docket entries")
        
        # Find matching orders
        matches = self.find_orders_by_date(docket_entries, order_dates)
        
        if not matches:
            print(f"    ⚠ No orders found for target dates")
            self.failed_downloads.append({
                'case_number': case_number,
                'reason': 'No orders found for specified dates'
            })
            return
        
        # Download each order
        downloaded = []
        for order_date, entry, recap_doc_urls in matches:
            # Fetch each RECAP document
            for recap_url in recap_doc_urls:
                print(f"      Fetching RECAP document: {recap_url}")
                doc = self.fetch_recap_document(recap_url)
                
                if not doc:
                    continue
                    
                if doc.get('is_available'):
                    # Get the PDF URL
                    pdf_url = doc.get('filepath_local')
                    if pdf_url:
                        # Create filename
                        date_str = order_date.replace(', ', '-').replace(' ', '-')
                        doc_num = doc.get('document_number', 'unk')
                        filename = f"order_{date_str}_doc{doc_num}.pdf"
                        output_path = os.path.join(case_dir, filename)
                        
                        # Download
                        if self.download_pdf(pdf_url, output_path):
                            downloaded.append({
                                'order_date': order_date,
                                'filename': filename,
                                'path': output_path,
                                'description': entry.get('description', '')
                            })
                            self.total_orders_downloaded += 1
                        
                        # Rate limit
                        time.sleep(self.RATE_LIMIT_DELAY)
        
        # Save case metadata
        case_info = {
            'case_number': case_number,
            'petitioner_name': petitioner,
            'primary_respondent': row['primary_respondent'],
            'judges': row['judges'],
            'docket_url': docket_url,
            'order_dates_expected': order_dates,
            'orders_expected': row['order_count'],
            'orders_downloaded': len(downloaded),
            'downloads': downloaded,
            'download_timestamp': datetime.now().isoformat()
        }
        
        info_path = os.path.join(case_dir, 'case_info.json')
        with open(info_path, 'w') as f:
            json.dump(case_info, f, indent=2)
        
        if downloaded:
            self.successful_downloads.append(case_info)
            print(f"    ✓ Downloaded {len(downloaded)} orders")
        else:
            self.failed_downloads.append({
                'case_number': case_number,
                'reason': 'Found orders but downloads failed'
            })
    
    def process_all_cases(self, test_mode=False):
        """Download orders for all found cases"""
        print("\n" + "="*80)
        print("DOWNLOADING COURT ORDERS")
        if test_mode:
            print("TEST MODE: Processing first case only")
        print("="*80)
        
        cases_to_process = self.found_df if not test_mode else self.found_df.head(1)
        
        for idx, row in cases_to_process.iterrows():
            print(f"\n[{idx+1}/{len(cases_to_process)}] Processing case...")
            self.process_case(row)
            
            # Save progress periodically (or after test)
            if test_mode or (idx + 1) % 10 == 0:
                self.save_progress()
        
        # Final save
        self.save_progress()
    
    def save_progress(self):
        """Save download progress"""
        progress = {
            'timestamp': datetime.now().isoformat(),
            'total_cases_searched': len(self.df),
            'cases_found_on_courtlistener': len(self.found_df),
            'cases_with_successful_downloads': len(self.successful_downloads),
            'cases_with_failed_downloads': len(self.failed_downloads),
            'total_orders_downloaded': self.total_orders_downloaded,
            'successful_downloads': self.successful_downloads,
            'failed_downloads': self.failed_downloads
        }
        
        progress_path = os.path.join(self.output_dir, 'download_progress.json')
        with open(progress_path, 'w') as f:
            json.dump(progress, f, indent=2)
    
    def generate_summary_report(self):
        """Generate comprehensive markdown summary"""
        print("\n" + "="*80)
        print("GENERATING SUMMARY REPORT")
        print("="*80)
        
        lines = []
        lines.append("# Minnesota Habeas Cases - Downloaded Orders Summary")
        lines.append(f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"\n## Overall Statistics")
        lines.append(f"\n- Total Cases in Appendix: {len(self.df)}")
        lines.append(f"- Cases Found on CourtListener: {len(self.found_df)}")
        lines.append(f"- Cases with Successfully Downloaded Orders: {len(self.successful_downloads)}")
        lines.append(f"- Total Orders Downloaded: {self.total_orders_downloaded}")
        lines.append(f"- Cases with Download Failures: {len(self.failed_downloads)}")
        
        # Success rate
        if len(self.found_df) > 0:
            success_rate = len(self.successful_downloads) / len(self.found_df) * 100
            lines.append(f"\n**Download Success Rate:** {success_rate:.1f}%")
        
        # Breakdown by respondent
        if self.successful_downloads:
            lines.append(f"\n## Downloads by Primary Respondent")
            respondent_counts = {}
            for case in self.successful_downloads:
                resp = case['primary_respondent']
                respondent_counts[resp] = respondent_counts.get(resp, 0) + 1
            
            for resp, count in sorted(respondent_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- {resp}: {count} cases")
        
        # Failed downloads
        if self.failed_downloads:
            lines.append(f"\n## Failed Downloads ({len(self.failed_downloads)} cases)")
            for failed in self.failed_downloads[:20]:  # Show first 20
                lines.append(f"- {failed['case_number']}: {failed.get('reason', 'Unknown')}")
            if len(self.failed_downloads) > 20:
                lines.append(f"- ... and {len(self.failed_downloads) - 20} more")
        
        # Next steps
        lines.append(f"\n## Next Steps")
        lines.append(f"\n1. **Review Downloaded Orders:**")
        lines.append(f"   - Check `{self.output_dir}/[case-number]/` folders")
        lines.append(f"   - Read order contents for violation details")
        lines.append(f"\n2. **Cross-Reference with Chicago Cases:**")
        lines.append(f"   - Compare personnel names (Bovino, etc.)")
        lines.append(f"   - Identify systematic patterns")
        lines.append(f"\n3. **Document Patterns:**")
        lines.append(f"   - Types of orders violated")
        lines.append(f"   - ICE response patterns")
        lines.append(f"   - Judge actions/sanctions")
        lines.append(f"\n4. **For Missing Cases:**")
        lines.append(f"   - Search PACER directly")
        lines.append(f"   - Contact attorneys listed in appendix")
        lines.append(f"   - Check if cases are sealed/restricted")
        
        # File locations
        lines.append(f"\n## Downloaded Files Location")
        lines.append(f"\nAll downloaded orders are in: `{self.output_dir}/`")
        lines.append(f"\nEach case has its own folder with:")
        lines.append(f"- `order_[date]_doc[num].pdf` - Court order PDFs")
        lines.append(f"- `case_info.json` - Case metadata and download details")
        
        report = '\n'.join(lines)
        
        # Save report
        report_path = os.path.join(self.output_dir, 'MINNESOTA_HABEAS_SUMMARY.md')
        with open(report_path, 'w') as f:
            f.write(report)
        
        print(report)
        print(f"\n✓ Summary saved: {report_path}")


def main():
    """Main entry point"""
    import sys
    
    # Check for API token
    api_token = os.environ.get('COURTLISTENER_API_TOKEN')
    
    csv_path = 'minnesota_habeas_cases.csv'
    test_mode = False
    
    # Parse arguments
    for arg in sys.argv[1:]:
        if arg == '--test':
            test_mode = True
        elif arg.endswith('.csv'):
            csv_path = arg
        elif not arg.startswith('--'):
            api_token = arg
    
    if not Path(csv_path).exists():
        print(f"Error: {csv_path} not found")
        print("\nMake sure you've run courtlistener_habeas_searcher.py first!")
        sys.exit(1)
    
    if not api_token:
        print("ERROR: CourtListener API token required!")
        print("\nUsage: python courtlistener_order_downloader.py [csv_path] [api_token] [--test]")
        print("  --test: Process only first case for debugging")
        print("\nOr set environment variable: COURTLISTENER_API_TOKEN")
        sys.exit(1)
    
    # Create downloader
    downloader = OrderDownloader(csv_path, api_token=api_token)
    
    if len(downloader.found_df) == 0:
        print("\n⚠ No cases found on CourtListener!")
        print("Run courtlistener_habeas_searcher.py first to search for cases.")
        sys.exit(1)
    
    # Download all orders (or test with first case)
    downloader.process_all_cases(test_mode=test_mode)
    
    # Generate summary
    downloader.generate_summary_report()
    
    print("\n" + "="*80)
    print("✓ DOWNLOAD COMPLETE")
    print("="*80)
    print(f"\nDownloaded {downloader.total_orders_downloaded} orders from {len(downloader.successful_downloads)} cases")
    print(f"Check: {downloader.output_dir}/")


if __name__ == "__main__":
    main()
