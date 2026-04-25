#!/usr/bin/env python3
"""
CourtListener Habeas Case Searcher
Searches CourtListener for Minnesota habeas cases and downloads violated orders

Features:
- Searches by case number in Minnesota District Court
- Identifies relevant court orders by date
- Downloads orders to organized folder structure
- Tracks search results and missing cases
"""

import pandas as pd
import requests
import os
import json
import time
from datetime import datetime
from pathlib import Path


class CourtListenerSearcher:
    """Search CourtListener for Minnesota habeas cases"""
    
    # CourtListener API base URL
    API_BASE = "https://www.courtlistener.com/api/rest/v4"
    
    # Rate limiting: CourtListener allows 5 requests/second for authenticated users
    RATE_LIMIT_DELAY = 0.25  # 4 requests per second to be safe
    
    def __init__(self, csv_path, api_token=None, output_dir='minnesota-habeas-orders'):
        """
        Initialize searcher
        
        Args:
            csv_path: Path to minnesota_habeas_cases.csv
            api_token: CourtListener API token (required)
            output_dir: Directory for downloaded orders
        """
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
        print(f"  Loaded {len(self.df)} cases")
        
        # Track results
        self.found_cases = []
        self.not_found_cases = []
        self.ambiguous_cases = []
        self.download_errors = []
    
    def search_case(self, case_number, petitioner_name):
        """
        Search CourtListener for a specific case
        
        Args:
            case_number: Case number (e.g., "26-CV-0107")
            petitioner_name: Petitioner name for fallback search
            
        Returns:
            dict with search results or None
        """
        # Normalize case number - CourtListener uses : instead of -
        # Example: 26-CV-0107 becomes 0:26-cv-00107
        normalized = self._normalize_case_number(case_number)
        
        print(f"\n  Searching for: {case_number} ({petitioner_name})")
        print(f"    Normalized: {normalized}")
        
        # Search by docket number in Minnesota District Court
        search_url = f"{self.API_BASE}/dockets/"
        params = {
            'docket_number': normalized,
            'court': 'mnd',  # Minnesota District Court
            'format': 'json'
        }
        
        try:
            response = requests.get(search_url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            results = data.get('results', [])
            
            if len(results) == 0:
                print(f"    ✗ Not found on CourtListener")
                return None
            elif len(results) == 1:
                docket = results[0]
                print(f"    ✓ Found: {docket['case_name']}")
                return docket
            else:
                print(f"    ⚠ Multiple matches ({len(results)})")
                # Return first match but flag as ambiguous
                return {'results': results, 'ambiguous': True}
                
        except Exception as e:
            print(f"    ✗ Search error: {e}")
            return None
    
    def _normalize_case_number(self, case_number):
        """
        Normalize case number to CourtListener format
        
        Examples:
            26-CV-0107 -> 0:26-cv-00107
            25-CV-4722 -> 0:25-cv-04722
        """
        # Remove any leading zeros from the case number part
        # Format: YY-CV-NNNNN
        parts = case_number.split('-')
        if len(parts) == 3:
            year = parts[0]
            cv = parts[1].lower()
            number = parts[2].zfill(5)  # Pad to 5 digits
            return f"0:{year}-{cv}-{number}"
        return case_number
    
    def get_docket_entries(self, docket_id):
        """
        Get all docket entries for a case
        
        Args:
            docket_id: CourtListener docket ID
            
        Returns:
            List of docket entries
        """
        url = f"{self.API_BASE}/dockets/{docket_id}/"
        params = {'format': 'json'}
        
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get('docket_entries', [])
        except Exception as e:
            print(f"    Error getting docket entries: {e}")
            return []
    
    def find_orders_by_date(self, docket_entries, order_dates_str):
        """
        Find docket entries matching order dates
        
        Args:
            docket_entries: List of docket entries
            order_dates_str: Semicolon-separated order dates (e.g., "January 15, 2026; January 19, 2026")
            
        Returns:
            List of matching docket entry URLs
        """
        order_dates = [d.strip() for d in order_dates_str.split(';')]
        
        # Convert order dates to YYYY-MM-DD format for comparison
        target_dates = []
        for date_str in order_dates:
            try:
                dt = datetime.strptime(date_str, "%B %d, %Y")
                target_dates.append(dt.strftime("%Y-%m-%d"))
            except:
                print(f"    Warning: Could not parse date: {date_str}")
        
        matching_entries = []
        for entry in docket_entries:
            entry_date = entry.get('date_filed', '')
            if entry_date in target_dates:
                # Check if it's an order (not just any docket entry)
                description = entry.get('description', '').lower()
                if 'order' in description:
                    matching_entries.append(entry)
                    print(f"    ✓ Found order: {entry_date} - {entry.get('description', '')[:60]}")
        
        return matching_entries
    
    def download_order(self, recap_document_url, output_path):
        """
        Download a court order PDF
        
        Args:
            recap_document_url: URL to RECAP document
            output_path: Where to save the PDF
            
        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.get(recap_document_url, timeout=60)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            return True
        except Exception as e:
            print(f"    ✗ Download failed: {e}")
            return False
    
    def process_all_cases(self):
        """Process all cases from CSV"""
        print("\n" + "="*80)
        print("SEARCHING COURTLISTENER FOR MINNESOTA HABEAS CASES")
        print("="*80)
        
        for idx, row in self.df.iterrows():
            case_number = row['case_number']
            petitioner = row['petitioner_name']
            order_dates = row['order_dates']
            
            print(f"\n[{idx+1}/{len(self.df)}] Processing: {case_number}")
            
            # Search for case
            result = self.search_case(case_number, petitioner)
            
            if result is None:
                self.not_found_cases.append(row.to_dict())
                self.df.at[idx, 'courtlistener_found'] = False
                self.df.at[idx, 'search_notes'] = 'Not found'
            elif result.get('ambiguous'):
                self.ambiguous_cases.append(row.to_dict())
                self.df.at[idx, 'courtlistener_found'] = True
                self.df.at[idx, 'search_notes'] = f'Multiple matches ({len(result["results"])})'
            else:
                # Found single match
                docket_url = result.get('absolute_url', '')
                docket_id = result.get('id')
                
                self.df.at[idx, 'courtlistener_found'] = True
                self.df.at[idx, 'docket_url'] = f"https://www.courtlistener.com{docket_url}"
                
                self.found_cases.append({
                    **row.to_dict(),
                    'docket_id': docket_id,
                    'docket_url': docket_url,
                    'case_name': result.get('case_name', '')
                })
            
            # Rate limiting
            time.sleep(self.RATE_LIMIT_DELAY)
        
        # Save updated CSV
        self.df.to_csv(self.csv_path, index=False)
        print(f"\n✓ Updated CSV saved: {self.csv_path}")
    
    def generate_summary(self):
        """Generate summary report"""
        print("\n" + "="*80)
        print("SEARCH SUMMARY")
        print("="*80)
        
        total = len(self.df)
        found = len(self.found_cases)
        not_found = len(self.not_found_cases)
        ambiguous = len(self.ambiguous_cases)
        
        print(f"\nTotal Cases Searched: {total}")
        print(f"  ✓ Found on CourtListener: {found} ({found/total*100:.1f}%)")
        print(f"  ✗ Not Found: {not_found} ({not_found/total*100:.1f}%)")
        print(f"  ⚠ Multiple Matches: {ambiguous} ({ambiguous/total*100:.1f}%)")
        
        if not_found > 0:
            print(f"\n--- Cases Not Found ---")
            for case in self.not_found_cases[:10]:  # Show first 10
                print(f"  • {case['case_number']}: {case['petitioner_name']}")
            if not_found > 10:
                print(f"  ... and {not_found - 10} more")
        
        # Save detailed results
        summary_file = os.path.join(self.output_dir, 'search_summary.json')
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_cases': total,
            'found': found,
            'not_found': not_found,
            'ambiguous': ambiguous,
            'found_cases': self.found_cases,
            'not_found_cases': self.not_found_cases,
            'ambiguous_cases': self.ambiguous_cases
        }
        
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"\n✓ Detailed results saved: {summary_file}")


def main():
    """Main entry point"""
    import sys
    
    # Check for API token
    api_token = os.environ.get('COURTLISTENER_API_TOKEN')
    
    if len(sys.argv) < 2:
        csv_path = 'minnesota_habeas_cases.csv'
        if not Path(csv_path).exists():
            print(f"Error: {csv_path} not found")
            print("\nUsage: python courtlistener_habeas_searcher.py [csv_path] [api_token]")
            print("\nOr set environment variable: COURTLISTENER_API_TOKEN")
            print("\nTo get an API token:")
            print("  1. Go to https://www.courtlistener.com/sign-in/")
            print("  2. Sign in or create account")
            print("  3. Go to https://www.courtlistener.com/api/rest-info/")
            print("  4. Generate API token")
            sys.exit(1)
    else:
        csv_path = sys.argv[1]
        if len(sys.argv) >= 3:
            api_token = sys.argv[2]
    
    if not api_token:
        print("ERROR: CourtListener API token required!")
        print("\nOption 1 - Command line:")
        print("  python courtlistener_habeas_searcher.py minnesota_habeas_cases.csv YOUR_API_TOKEN")
        print("\nOption 2 - Environment variable:")
        print("  Windows: set COURTLISTENER_API_TOKEN=your_token_here")
        print("  Linux/Mac: export COURTLISTENER_API_TOKEN=your_token_here")
        print("  Then run: python courtlistener_habeas_searcher.py minnesota_habeas_cases.csv")
        print("\nTo get an API token:")
        print("  1. Go to https://www.courtlistener.com/sign-in/")
        print("  2. Sign in or create a free account")
        print("  3. Go to https://www.courtlistener.com/api/rest-info/")
        print("  4. Click 'Generate New Token' or copy existing token")
        sys.exit(1)
    
    # Create searcher and process
    searcher = CourtListenerSearcher(csv_path, api_token=api_token)
    searcher.process_all_cases()
    searcher.generate_summary()
    
    print("\n" + "="*80)
    print("✓ SEARCH COMPLETE")
    print("="*80)
    print(f"\nNext step: Review results in {searcher.output_dir}/search_summary.json")
    print("Then run order downloader to get the actual PDFs")


if __name__ == "__main__":
    main()
